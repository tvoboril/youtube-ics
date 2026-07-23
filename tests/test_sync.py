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


def test_office_reshape_updates_shared_broadcast_without_cancelling_it():
    """A standalone Orthros that later merges into Orthros+Liturgy.

    The merged key reuses the same-slot broadcast and pushes the merged title/description onto
    it (so it doesn't stay stuck as "Orthros"), while the old standalone key's disappearance
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
    assert summary.updated == 1 and summary.cancelled == 0
    assert sink.updated[0][0] == yt  # merged title pushed onto the same broadcast
    assert not sink.cancelled  # the broadcast was never deleted
    assert any(e.youtube_id == yt and e.title == merged.title for e in sink.existing)
    assert store.get(merged.key).youtube_id == yt  # merged key now points at it
    assert store.get(merged.key).title == merged.title
    # A second identical sync settles to a plain no-op.
    assert _reconcile([merged], store, sink).unchanged == 1


def test_office_reshape_preserves_operator_rename():
    """If the operator renamed the broadcast in Studio before it reshaped, don't clobber it."""
    store, sink = Store(":memory:"), FakeSink()
    standalone = _pb(
        "uidA", 2026, 7, 12, title="Orthros", office=Office.ORTHROS, uids=["uidA"]
    )
    _reconcile([standalone], store, sink)
    yt = store.get(standalone.key).youtube_id
    sink.existing[0].title = "Orthros (special)"  # operator rename on the channel

    merged = _pb(
        "uidA", 2026, 7, 12, title="Orthros and Divine Liturgy",
        office=Office.ORTHROS_LITURGY, uids=["uidA", "uidB"],
    )
    summary = _reconcile([merged], store, sink)
    assert summary.adopted == 1 and summary.updated == 0
    assert not sink.updated  # the operator's title is left untouched
    assert store.get(merged.key).youtube_id == yt
    assert store.get(merged.key).title == "Orthros (special)"


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


def test_stale_upcoming_ghost_is_reaped():
    """A past broadcast stuck 'upcoming' (never went live) is deleted so the shared-stream
    autostart can't silently transition it live — the "fired a second time" bug."""
    store, sink = Store(":memory:"), FakeSink()
    sink.existing = [ExistingBroadcast("ghost-yt", "Orthros - 1 July 2026", "2026-07-01T14:00:00Z")]
    summary = _reconcile([_pb("a", 2026, 7, 12)], store, sink)
    assert summary.reaped == 1
    assert "ghost-yt" in sink.cancelled
    assert summary.created == 1  # the real plan item is still created alongside


def test_broadcast_scheduled_today_is_not_reaped_even_if_late():
    """A service scheduled earlier *today* (its start already past because it began late, or
    hasn't started yet) is never reaped — we only reap broadcasts from a previous day."""
    # WIN_START is 2026-07-11T00:00Z = 2026-07-10 19:00 parish-local, so "today" is Jul 10.
    store, sink = Store(":memory:"), FakeSink()
    # scheduled 09:00 parish-local the same day (14:00Z), well before "now" — a very late start.
    sink.existing = [ExistingBroadcast("today-yt", "Orthros", "2026-07-10T14:00:00Z")]
    summary = _reconcile([], store, sink)
    assert summary.reaped == 0
    assert not sink.cancelled


def test_broadcast_scheduled_yesterday_is_reaped():
    """A ghost from a previous calendar day is reaped even if it was only hours ago."""
    # "today" is Jul 10 parish-local; this start is late on Jul 9 (Jul 10 01:00Z), a prior day.
    store, sink = Store(":memory:"), FakeSink()
    sink.existing = [ExistingBroadcast("yesterday-yt", "Vespers", "2026-07-10T01:00:00Z")]
    summary = _reconcile([], store, sink)
    assert summary.reaped == 1
    assert "yesterday-yt" in sink.cancelled


def test_reap_leaves_persistent_no_start_broadcast_alone():
    """A reusable/persistent broadcast has no scheduled start — never reap it."""
    store, sink = Store(":memory:"), FakeSink()
    sink.existing = [ExistingBroadcast("persistent-yt", "Saint George Live", "")]
    summary = _reconcile([], store, sink)
    assert summary.reaped == 0
    assert not sink.cancelled


def test_reap_skips_a_past_slot_the_plan_still_depends_on():
    """If a surviving planned key adopts a past-dated slot, don't reap the broadcast under it."""
    store, sink = Store(":memory:"), FakeSink()
    p = _pb("a", 2026, 7, 1, title="Vespers - 1 July 2026")
    sink.existing = [ExistingBroadcast("shared-yt", p.title, p.start_utc.isoformat())]
    summary = _reconcile([p], store, sink)
    assert summary.reaped == 0 and summary.adopted == 1
    assert "shared-yt" not in sink.cancelled


def test_cancelled_then_reappears_is_recreated():
    store, sink = Store(":memory:"), FakeSink()
    _reconcile([_pb("a", 2026, 7, 12)], store, sink)
    _reconcile([], store, sink)  # cancels it
    summary = _reconcile([_pb("a", 2026, 7, 12)], store, sink)
    assert summary.created == 1  # cancelled rows are re-created, not left dangling
    assert len(sink.created) == 2
