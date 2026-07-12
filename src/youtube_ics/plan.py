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
