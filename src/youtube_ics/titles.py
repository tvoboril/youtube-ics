"""Build YouTube titles and descriptions from a Broadcast + liturgical enrichment."""

from __future__ import annotations

from datetime import date

from .enrich import LiturgicalInfo
from .models import Broadcast, Office

FOOTER = """Streaming liturgical celebrations live from St. George the Great Martyr Melkite Catholic Church in Birmingham, Alabama.

Website: https://www.saintgeorgeonline.org/
Facebook: https://www.facebook.com/StGeorgeMelkite
Instagram: https://www.instagram.com/stgeorgefoodfest/
YouTube: https://www.youtube.com/@saintgeorgemelkite

Melkite Catholic Eparchy of Newton: https://melkite.org/
Melkite Catholic Patriarchate: http://www.melkitepat.org/"""


def office_label(bc: Broadcast, info: LiturgicalInfo) -> str:
    """The office prefix. Adds the tone for the Sunday combo, and upgrades a plain Vespers
    to 'Great Vespers' when the day it ushers in is a feast (typikon has a primary feast).
    `info` is already the commemoration-day enrichment (next day for Vespers)."""
    if bc.office is Office.ORTHROS_LITURGY and info.tone:
        return f"Orthros (Tone {info.tone}) and Divine Liturgy"
    if bc.office is Office.VESPERS:
        if info.vespers_rank is not None:
            return "Great Vespers" if info.vespers_rank == "great" else "Vespers"
        # Fallback if the API omits a rank: a feast on the day ⇒ Great Vespers.
        if info.primary_feast or bc.office_label == "Great Vespers":
            return "Great Vespers"
        return "Vespers"
    return bc.office_label


def commemoration(bc: Broadcast, info: LiturgicalInfo) -> str:
    """Title's middle segment. Typikon precedence: a named feast wins; else, on Sundays,
    the 'Nth Sunday after Pentecost' counter; else the first saint of the day."""
    if bc.office is Office.OTHER:
        return ""  # Paraklisis/Akathist/etc.: name + date only
    if info.primary_feast:
        return info.primary_feast
    if bc.office is Office.ORTHROS_LITURGY:
        sun = info.sunday_name
        if sun:
            return sun
    if info.saints:
        return info.saints[0]
    return ""


def _fmt_date(d: date) -> str:
    return f"{d.day} {d:%B %Y}"  # e.g. "5 July 2026"


TITLE_MAX = 100  # YouTube's hard limit on video/broadcast titles


def build_title(bc: Broadcast, info: LiturgicalInfo) -> str:
    office = office_label(bc, info)
    date = _fmt_date(bc.local_date)
    commem = commemoration(bc, info)
    if not commem:
        return f"{office} - {date}"[:TITLE_MAX]
    full = f"{office} - {commem} - {date}"
    if len(full) <= TITLE_MAX:
        return full
    # Too long: trim the commemoration (on a word boundary), keeping office + date intact.
    overhead = len(office) + len(date) + len(" - ") * 2 + len("…")
    trimmed = commem[: max(0, TITLE_MAX - overhead)].rstrip()
    if " " in trimmed:
        trimmed = trimmed[: trimmed.rfind(" ")].rstrip()
    return f"{office} - {trimmed}… - {date}"


def build_description(bc: Broadcast, info: LiturgicalInfo) -> str:
    lines = [build_title(bc, info)]
    if bc.office is not Office.OTHER and info.saints:
        lines.append("")
        lines.extend(info.saints)
    lines.append("")
    lines.append(FOOTER)
    return "\n".join(lines)
