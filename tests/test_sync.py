from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from youtube_ics.models import Broadcast, Office
from youtube_ics.plan import PlannedBroadcast
from youtube_ics.sink import FakeSink
from youtube_ics.store import Store
from youtube_ics.sync import reconcile

CT = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")
WIN_START = datetime(2026, 7, 11, tzinfo=UTC)
WIN_END = datetime(2026, 7, 25, tzinfo=UTC)


def _pb(uid, y, m, d, title="Some Title", desc="desc"):
    start = datetime(y, m, d, 9, 0, tzinfo=CT)
    bc = Broadcast(Office.VESPERS, "Vespers", start, start + timedelta(hours=1), source_uids=[uid])
    return PlannedBroadcast(broadcast=bc, title=title, description=desc)


def _reconcile(plan, store, sink, **kw):
    return reconcile(plan, store, sink, window_start_utc=WIN_START, window_end_utc=WIN_END, **kw)


def test_first_sync_creates_everything():
    store, sink = Store(":memory:"), FakeSink()
    plan = [_pb("a", 2026, 7, 12), _pb("b", 2026, 7, 15)]
    summary = _reconcile(plan, store, sink)
    assert (summary.created, summary.updated, summary.cancelled) == (2, 0, 0)
    assert len(sink.created) == 2
    assert store.get(plan[0].key).youtube_id == "fake-yt-1"


def test_second_identical_sync_is_all_noop():
    store, sink = Store(":memory:"), FakeSink()
    plan = [_pb("a", 2026, 7, 12), _pb("b", 2026, 7, 15)]
    _reconcile(plan, store, sink)
    sink2 = FakeSink()
    summary = _reconcile(plan, store, sink2)
    assert summary.unchanged == 2
    assert not sink2.created and not sink2.updated and not sink2.cancelled


def test_changed_title_triggers_update_same_youtube_id():
    store, sink = Store(":memory:"), FakeSink()
    _reconcile([_pb("a", 2026, 7, 12, title="Old")], store, sink)
    yt = store.get(_pb("a", 2026, 7, 12).key).youtube_id
    sink2 = FakeSink()
    summary = _reconcile([_pb("a", 2026, 7, 12, title="New")], store, sink2)
    assert summary.updated == 1
    assert sink2.updated[0][0] == yt  # same broadcast id reused
    assert store.get(_pb("a", 2026, 7, 12).key).title == "New"


def test_vanished_in_window_is_cancelled():
    store, sink = Store(":memory:"), FakeSink()
    _reconcile([_pb("a", 2026, 7, 12), _pb("b", 2026, 7, 15)], store, sink)
    # 'b' disappears from the plan (e.g. calendar event deleted / untagged)
    sink2 = FakeSink()
    summary = _reconcile([_pb("a", 2026, 7, 12)], store, sink2)
    assert summary.cancelled == 1
    assert len(sink2.cancelled) == 1
    assert store.get(_pb("b", 2026, 7, 15).key).status == "cancelled"


def test_vanished_outside_window_is_not_cancelled():
    store, sink = Store(":memory:"), FakeSink()
    # seed a future broadcast beyond the window
    _reconcile([_pb("far", 2026, 9, 1)], store, sink)
    sink2 = FakeSink()
    summary = _reconcile([], store, sink2)  # empty plan
    assert summary.cancelled == 0  # out-of-window row untouched
    assert store.get(_pb("far", 2026, 9, 1).key).status == "scheduled"


def test_dry_run_mutates_nothing():
    store, sink = Store(":memory:"), FakeSink()
    plan = [_pb("a", 2026, 7, 12)]
    summary = _reconcile(plan, store, sink, dry_run=True)
    assert summary.created == 1
    assert not sink.created  # sink untouched
    assert store.get(plan[0].key) is None  # store untouched


def test_cancelled_then_reappears_is_recreated():
    store, sink = Store(":memory:"), FakeSink()
    _reconcile([_pb("a", 2026, 7, 12)], store, sink)
    _reconcile([], store, FakeSink())  # cancels it
    sink3 = FakeSink()
    summary = _reconcile([_pb("a", 2026, 7, 12)], store, sink3)
    assert summary.created == 1  # cancelled rows are re-created, not left dangling
    assert len(sink3.created) == 1
