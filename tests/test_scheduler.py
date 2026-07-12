from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from youtube_ics.models import Broadcast, Office
from youtube_ics.plan import PlannedBroadcast
from youtube_ics.scheduler import next_run_at

CT = ZoneInfo("America/Chicago")


def _pb(y, m, d, hh, mm=0):
    start = datetime(y, m, d, hh, mm, tzinfo=CT)
    bc = Broadcast(Office.VESPERS, "Vespers", start, None)
    return PlannedBroadcast(broadcast=bc, title="t", description="d")


def test_wakes_15min_before_an_imminent_event():
    now = datetime(2026, 7, 12, 8, 0, tzinfo=CT)  # event within the hour
    plan = [_pb(2026, 7, 12, 8, 30), _pb(2026, 7, 15, 18, 0)]
    assert next_run_at(plan, now) == datetime(2026, 7, 12, 8, 15, tzinfo=CT)


def test_hourly_cap_when_next_event_is_far():
    now = datetime(2026, 7, 11, 8, 0, tzinfo=CT)  # next event days away
    plan = [_pb(2026, 7, 12, 9, 0), _pb(2026, 7, 15, 18, 0)]
    assert next_run_at(plan, now) == now + timedelta(hours=1)  # safety-net check


def test_skips_events_whose_lead_window_already_opened():
    now = datetime(2026, 7, 12, 8, 50, tzinfo=CT)  # inside the 9:00 event's 15-min lead
    plan = [_pb(2026, 7, 12, 9, 0), _pb(2026, 7, 15, 18, 0)]
    # 9:00 is skipped (handled this run); next real event is far → hourly ceiling
    assert next_run_at(plan, now) == now + timedelta(hours=1)


def test_hourly_when_no_upcoming_events():
    now = datetime(2026, 7, 11, 8, 0, tzinfo=CT)
    assert next_run_at([], now) == now + timedelta(hours=1)
