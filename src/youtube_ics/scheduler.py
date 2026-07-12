"""Event-relative scheduling: wake 15 minutes before the next scheduled broadcast.

Not a fixed poll — each reconcile computes the next wake from the plan itself. There is
always a Sunday/Wednesday service inside the 2-week window, so the chain is self-sustaining;
`FALLBACK` covers the degenerate case where nothing is scheduled at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .plan import PlannedBroadcast

LEAD = timedelta(minutes=15)  # how far before an event to run the pre-event check
FALLBACK = timedelta(hours=24)  # re-check cadence when no upcoming event exists


def next_run_at(
    plan: list[PlannedBroadcast],
    now: datetime,
    *,
    lead: timedelta = LEAD,
    fallback: timedelta = FALLBACK,
) -> datetime:
    """When to wake next: the earliest (event start − lead) still in the future.

    Events whose lead window has already opened (start − lead <= now) are considered
    handled by the current run and skipped. Falls back to now+fallback when nothing's left.
    """
    candidates = [
        p.broadcast.start - lead for p in plan if (p.broadcast.start - lead) > now
    ]
    return min(candidates) if candidates else now + fallback
