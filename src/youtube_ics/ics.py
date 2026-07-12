"""Fetch, expand, filter, and assemble the ICS feed into Broadcast plans."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events

from .models import Broadcast, Occurrence, Office

PARISH_TZ = ZoneInfo("America/Chicago")

# Office keywords, used to pick the title prefix + drive the Sunday merge.
_KW_VESPERS = re.compile(r"\bvespers\b", re.I)
_KW_ORTHROS = re.compile(r"\borthros\b", re.I)
_KW_LITURGY = re.compile(r"\b(divine\s+liturgy|liturgy)\b", re.I)

# The operator marks events to stream by putting this tag in the title, e.g.
# "Divine Liturgy (Livestreamed)". It is the single source of truth for inclusion —
# Saturdays etc. simply go untagged. Lenient on case and inner spacing.
_TAG_RE = re.compile(r"\(\s*live\s*stream(?:ed)?\s*\)", re.I)


def is_livestream_tagged(summary: str) -> bool:
    return bool(_TAG_RE.search(summary))


def strip_tag(summary: str) -> str:
    """Remove the livestream tag and tidy leftover separators/whitespace."""
    cleaned = _TAG_RE.sub("", summary)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"[\s\-–—]+$", "", cleaned)  # trailing space/dash left behind
    return cleaned.strip()


def fetch_ics(url: str, *, timeout: float = 30.0) -> icalendar.Calendar:
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return icalendar.Calendar.from_ical(resp.content)


def load_ics(data: bytes | str) -> icalendar.Calendar:
    return icalendar.Calendar.from_ical(data)


def _recurring_uids(cal: icalendar.Calendar) -> set[str]:
    """UIDs of master VEVENTs that carry an RRULE (the generic weekly services).

    recurring_ical_events strips RRULE from the expanded instance copies, so we identify
    recurrence from the masters up front and match expanded occurrences back by UID.
    """
    uids: set[str] = set()
    for comp in cal.walk("VEVENT"):
        if comp.get("RRULE") is not None:
            uid = comp.get("UID")
            if uid is not None:
                uids.add(str(uid))
    return uids


def expand_office_occurrences(
    cal: icalendar.Calendar, window_start: datetime, window_end: datetime
) -> list[Occurrence]:
    """Expand RRULEs within the window and keep only office services (filtered)."""
    recurring = _recurring_uids(cal)
    events = recurring_ical_events.of(cal).between(window_start, window_end)
    out: list[Occurrence] = []
    for ev in events:
        raw_summary = str(ev.get("SUMMARY", "")).strip()
        if not is_livestream_tagged(raw_summary):
            continue
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue  # treated as vanished → reconcile cancels any existing broadcast
        summary = strip_tag(raw_summary)  # office classification/labels use the clean name
        start = _aware(ev.get("DTSTART"))
        if start is None:
            continue  # all-day / date-only feast banners are not streamable services
        end = _aware(ev.get("DTEND"))
        uid = str(ev.get("UID", "")) or f"nouid:{summary}:{start.isoformat()}"
        out.append(
            Occurrence(
                uid=uid,
                summary=summary,
                start=start,
                end=end,
                is_recurring=uid in recurring,
            )
        )
    out.sort(key=lambda o: o.start)
    return out


def _aware(prop) -> datetime | None:
    """Coerce an icalendar DTSTART/DTEND to a timezone-aware datetime, or None if date-only."""
    if prop is None:
        return None
    val = prop.dt
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=PARISH_TZ)
        return val
    return None  # datetime.date (all-day) — skip


def _office_of(summary: str) -> Office:
    has_v = bool(_KW_VESPERS.search(summary))
    has_o = bool(_KW_ORTHROS.search(summary))
    has_l = bool(_KW_LITURGY.search(summary))
    if has_v:
        return Office.VESPERS
    if has_o and has_l:
        return Office.ORTHROS_LITURGY
    if has_o:
        return Office.ORTHROS
    if has_l:
        return Office.LITURGY
    return Office.OTHER  # Paraklisis, Akathist, etc. — no office keyword


def _label_for(office: Office, summary: str, *, tone: int | None = None) -> str:
    if office is Office.VESPERS:
        return "Great Vespers" if re.search(r"\bgreat\s+vespers\b", summary, re.I) else "Vespers"
    if office is Office.ORTHROS_LITURGY:
        tone_str = f" (Tone {tone})" if tone else ""
        return f"Orthros{tone_str} and Divine Liturgy"
    if office is Office.ORTHROS:
        return "Orthros"
    if office is Office.LITURGY:
        return "Divine Liturgy"
    return summary  # OTHER: use the calendar's own service name verbatim


def assemble_broadcasts(occurrences: list[Occurrence]) -> list[Broadcast]:
    """Merge Sunday Orthros+Liturgy, then let specific feast events supersede recurring."""
    # 1) Merge back-to-back Sunday Orthros + Divine Liturgy into one combined broadcast.
    merged: list[Broadcast] = []
    consumed: set[int] = set()
    for i, occ in enumerate(occurrences):
        if i in consumed:
            continue
        off = _office_of(occ.summary)
        if off is Office.ORTHROS:
            partner = _find_following_liturgy(occurrences, i, consumed)
            if partner is not None:
                j, lit = partner
                consumed.add(j)
                merged.append(
                    Broadcast(
                        office=Office.ORTHROS_LITURGY,
                        office_label=_label_for(Office.ORTHROS_LITURGY, occ.summary),
                        start=occ.start,
                        end=lit.end,
                        source_uids=[occ.uid, lit.uid],
                        from_feast=not (occ.is_recurring and lit.is_recurring),
                    )
                )
                continue
        merged.append(
            Broadcast(
                office=off,
                office_label=_label_for(off, occ.summary),
                start=occ.start,
                end=occ.end,
                source_uids=[occ.uid],
                from_feast=not occ.is_recurring,
            )
        )

    # 2) Feast supersedes generic recurring for the same (date, office) pair.
    by_key: dict[tuple, list[Broadcast]] = {}
    for b in merged:
        by_key.setdefault((b.local_date, _office_bucket(b.office)), []).append(b)
    result: list[Broadcast] = []
    for group in by_key.values():
        if len(group) == 1:
            result.append(group[0])
            continue
        feasts = [b for b in group if b.from_feast]
        result.extend(feasts if feasts else group)
    result.sort(key=lambda b: b.start)
    return result


def _office_bucket(office: Office) -> str:
    """Group ORTHROS_LITURGY/LITURGY/ORTHROS as 'liturgy-ish' vs 'vespers' for supersede."""
    return "vespers" if office is Office.VESPERS else "liturgy"


def _find_following_liturgy(occ: list[Occurrence], i: int, consumed: set[int]):
    """Find a same-day Divine Liturgy starting when the Orthros ends (or right after)."""
    orthros = occ[i]
    for j in range(len(occ)):
        if j == i or j in consumed:
            continue
        cand = occ[j]
        if cand.local_date != orthros.local_date:
            continue
        if _office_of(cand.summary) is not Office.LITURGY:
            continue
        if orthros.end is None:
            return (j, cand)
        gap = abs((cand.start - orthros.end).total_seconds())
        if gap <= 1800:  # within 30 min of Orthros ending
            return (j, cand)
    return None
