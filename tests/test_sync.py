from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from youtube_ics.models import Broadcast, Office
from youtube_ics.plan import PlannedBroadcast
from youtube_ics.sink import ExistingBroadcast, FakeSink
from youtube_ics.store import Store
from youtube_ics.sync import reconcile

CT = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")
WIN_START = datetime(2026, 7, 11, tzinfo=UTC)
WIN_END = datetime(2026, 7, 25, tzinfo=UTC)


def _pb(uid, y, m, d, title="Some Title", desc="desc", office=Office.VESPERS, uids=None):
    start = datetime(y, m, d, 9, 0, tzinfo=CT)
    bc = Broadcast(
        office, "label", start, start + timedelta(hours=1), source_uids=uids or [uid]
    )
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
    summary = _reconcile(plan, store, sink)  # same sink → channel state persists
    assert summary.unchanged == 2
    assert not sink.updated and not sink.cancelled
    assert len(sink.created) == 2  # nothing new created


def test_changed_title_triggers_update_same_youtube_id():
    store, sink = Store(":memory:"), FakeSink()
    _reconcile([_pb("a", 2026, 7, 12, title="Old")], store, sink)
    yt = store.get(_pb("a", 2026, 7, 12).key).youtube_id
    # A plan-driven title change, with the channel title still what we last wrote, updates.
    summary = _reconcile([_pb("a", 2026, 7, 12, title="New")], store, sink)
    assert summary.updated == 1
    assert sink.updated[0][0] == yt  # same broadcast id reused
    assert store.get(_pb("a", 2026, 7, 12).key).title == "New"


def test_manual_title_edit_in_studio_is_respected():
    """Once created, an operator rename in Studio is never reverted or re-created."""
    store, sink = Store(":memory:"), FakeSink()
    _reconcile([_pb("a", 2026, 7, 12, title="Auto Title")], store, sink)
    # Operator renames the broadcast on the channel (e.g. "Third Hour and Divine Liturgy").
    sink.existing[0].title = "Third Hour and Divine Liturgy"
    # Even though the plan now wants a different generated title, we leave the channel alone.
    summary = _reconcile([_pb("a", 2026, 7, 12, title="Auto Title v2")], store, sink)
    assert summary.unchanged == 1 and summary.updated == 0
    assert not sink.updated  # no write to YouTube
    assert sink.existing[0].title == "Third Hour and Divine Liturgy"  # rename preserved


def test_deleted_broadcast_is_recreated():
    """A tracked broadcast that vanished from the channel self-heals instead of staying NOOP."""
    store, sink = Store(":memory:"), FakeSink()
    _reconcile([_pb("a", 2026, 7, 12)], store, sink)
    sink.existing.clear()  # broadcast disappeared from the channel (deleted / lost)
    summary = _reconcile([_pb("a", 2026, 7, 12)], store, sink)
    assert summary.created == 1
    assert len(sink.created) == 2  # a fresh broadcast was made
    assert store.get(_pb("a", 2026, 7, 12).key).youtube_id == "fake-yt-2"


def test_office_reshape_adopts_shared_broadcast_without_cancelling_it():
    """The July-26 regression: a standalone Orthros that later merges into Orthros+Liturgy.

    The merged key adopts the same-slot broadcast; the old standalone key's disappearance
    must NOT cancel (delete) the broadcast the merged key now depends on.
    """
    store, sink = Store(":memory:"), FakeSink()
    standalone = _pb(
        "uidA", 2026, 7, 12, title="Orthros", office=Office.ORTHROS, uids=["uidA"]
    )
    _reconcile([standalone], store, sink)
    yt = store.get(standalone.key).youtube_id  # fake-yt-1

    merged = _pb(
        "uidA", 2026, 7, 12, title="Orthros and Divine Liturgy",
        office=Office.ORTHROS_LITURGY, uids=["uidA", "uidB"],
    )
    summary = _reconcile([merged], store, sink)
    assert summary.adopted == 1 and summary.cancelled == 0
    assert not sink.cancelled  # the broadcast was never deleted
    assert any(e.youtube_id == yt for e in sink.existing)  # still on the channel
    assert store.get(merged.key).youtube_id == yt  # merged key now points at it


def test_vanished_in_window_is_cancelled():
    store, sink = Store(":memory:"), FakeSink()
    _reconcile([_pb("a", 2026, 7, 12), _pb("b", 2026, 7, 15)], store, sink)
    # 'b' disappears from the plan (e.g. calendar event deleted / untagged)
    summary = _reconcile([_pb("a", 2026, 7, 12)], store, sink)
    assert summary.cancelled == 1
    assert len(sink.cancelled) == 1
    assert store.get(_pb("b", 2026, 7, 15).key).status == "cancelled"


def test_vanished_outside_window_is_not_cancelled():
    store, sink = Store(":memory:"), FakeSink()
    # seed a future broadcast beyond the window
    _reconcile([_pb("far", 2026, 9, 1)], store, sink)
    summary = _reconcile([], store, sink)  # empty plan
    assert summary.cancelled == 0  # out-of-window row untouched
    assert store.get(_pb("far", 2026, 9, 1).key).status == "scheduled"


def test_dry_run_mutates_nothing():
    store, sink = Store(":memory:"), FakeSink()
    plan = [_pb("a", 2026, 7, 12)]
    summary = _reconcile(plan, store, sink, dry_run=True)
    assert summary.created == 1
    assert not sink.created  # sink untouched
    assert store.get(plan[0].key) is None  # store untouched


def test_empty_store_adopts_existing_broadcast_instead_of_duplicating():
    # Simulates a lost/rebuilt store: the broadcast already exists on the channel.
    store, sink = Store(":memory:"), FakeSink()
    p = _pb("a", 2026, 7, 12, title="Vespers - X - 12 July 2026")
    sink.existing = [ExistingBroadcast("existing-yt-1", p.title, p.start_utc.isoformat())]
    summary = _reconcile([p], store, sink)
    assert (summary.created, summary.adopted) == (0, 1)  # adopted, not created
    assert not sink.created
    assert store.get(p.key).youtube_id == "existing-yt-1"  # mapping recorded
    # a second run is a plain no-op now that it's in the store
    summary2 = _reconcile([p], store, sink)
    assert summary2.unchanged == 1


def test_adopt_preserves_existing_title_rather_than_overwriting():
    """On a lost store, the channel title may be operator-set — adopt without clobbering it."""
    store, sink = Store(":memory:"), FakeSink()
    p = _pb("a", 2026, 7, 12, title="New Title")
    sink.existing = [ExistingBroadcast("existing-yt-9", "Operator Title", p.start_utc.isoformat())]
    summary = _reconcile([p], store, sink)
    assert summary.adopted == 1 and summary.updated == 0
    assert not sink.updated  # no overwrite of the channel title
    rec = store.get(p.key)
    assert rec.youtube_id == "existing-yt-9"
    assert rec.title == "Operator Title"  # channel title is recorded, not the plan's


def test_no_adopt_when_start_differs():
    store, sink = Store(":memory:"), FakeSink()
    p = _pb("a", 2026, 7, 12)
    sink.existing = [ExistingBroadcast("other", p.title, "2020-01-01T00:00:00Z")]
    summary = _reconcile([p], store, sink)
    assert summary.created == 1 and summary.adopted == 0  # different slot → real create


def test_cancelled_then_reappears_is_recreated():
    store, sink = Store(":memory:"), FakeSink()
    _reconcile([_pb("a", 2026, 7, 12)], store, sink)
    _reconcile([], store, sink)  # cancels it
    summary = _reconcile([_pb("a", 2026, 7, 12)], store, sink)
    assert summary.created == 1  # cancelled rows are re-created, not left dangling
    assert len(sink.created) == 2
