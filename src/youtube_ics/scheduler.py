"""When to wake the run loop next.

Two goals: (1) wake 15 minutes before each scheduled broadcast for a last-minute
cancellation check, and (2) re-check at least hourly so an event added to the calendar
mid-day is picked up promptly (rather than waiting until the next already-scheduled event).
So the next wake is the earlier of "15 min before the next event" and "an hour from now".
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .plan import PlannedBroadcast

LEAD = timedelta(minutes=15)  # how far before an event to run the pre-event check
MAX_INTERVAL = timedelta(hours=1)  # safety-net ceiling: never sleep longer than this


def next_run_at(
    plan: list[PlannedBroadcast],
    now: datetime,
    *,
    lead: timedelta = LEAD,
    max_interval: timedelta = MAX_INTERVAL,
) -> datetime:
    """The earlier of (next event start − lead) and (now + max_interval).

    Events whose lead window has already opened (start − lead <= now) are considered handled
    by the current run and skipped. With no upcoming event, falls back to the hourly ceiling.
    """
    ceiling = now + max_interval
    candidates = [
        p.broadcast.start - lead for p in plan if (p.broadcast.start - lead) > now
    ]
    return min(min(candidates), ceiling) if candidates else ceiling
