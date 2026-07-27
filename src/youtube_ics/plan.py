"""Assemble the full broadcast plan (calendar + typikon enrichment) for a window."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import ics
from .config import Config
from .enrich import ApiTypikonSource, LiturgicalInfo, TypikonSource
from .models import Broadcast
from .titles import build_description, build_title

UTC = ZoneInfo("UTC")


@dataclass
class PlannedBroadcast:
    broadcast: Broadcast
    title: str
    description: str

    @property
    def start_utc(self) -> datetime:
        return self.broadcast.start.astimezone(UTC)

    @property
    def key(self) -> str:
        return self.broadcast.key

    @property
    def content_hash(self) -> str:
        """Signature of what YouTube stores; a change here means the broadcast needs an
        update. Covers title, description, and scheduled start (RFC3339 UTC)."""
        payload = f"{self.title}\x00{self.description}\x00{self.start_utc.isoformat()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_plan(
    cfg: Config,
    *,
    now: datetime | None = None,
    cal=None,
    typikon: TypikonSource | None = None,
) -> list[PlannedBroadcast]:
    now = now or datetime.now(ics.PARISH_TZ)
    window_end = now + timedelta(days=cfg.lookahead_days)
    cal = cal if cal is not None else ics.fetch_ics(cfg.ics_url)
    typikon = typikon or ApiTypikonSource(cfg.typikon_api_url)

    occurrences = ics.expand_office_occurrences(cal, now, window_end)
    broadcasts = ics.assemble_broadcasts(occurrences)

    # Drop services that have already started. `expand` returns occurrences that *overlap* the
    # window (recurring_ical_events semantics), so an in-progress service is still present — but
    # once it starts its broadcast has gone 'active' (off list_upcoming), and planning it again
    # makes reconcile self-heal a duplicate bound to the shared stream. Filtering here, after the
    # merge, judges the service as a whole: a merged Sunday's start is its Orthros start, so once
    # that begins the whole unit drops — no lone-Liturgy fragment survives to be created solo.
    broadcasts = [bc for bc in broadcasts if bc.start >= now]

    plan: list[PlannedBroadcast] = []
    for bc in broadcasts:
        try:
            info = typikon.get(bc.commemoration_date())
        except Exception:  # noqa: BLE001 - enrichment is best-effort; title degrades gracefully
            info = LiturgicalInfo()
        plan.append(
            PlannedBroadcast(
                broadcast=bc,
                title=build_title(bc, info),
                description=build_description(bc, info),
            )
        )
    return plan
