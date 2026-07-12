from datetime import datetime
from zoneinfo import ZoneInfo

from youtube_ics.models import Broadcast, Office
from youtube_ics.store import Store

CT = ZoneInfo("America/Chicago")


def _store():
    return Store(":memory:")


def test_upsert_and_get_roundtrip():
    with _store() as s:
        s.upsert("k1", "yt_abc", "Title", "2026-07-12T14:00:00+00:00", "hash1")
        rec = s.get("k1")
        assert rec is not None
        assert rec.youtube_id == "yt_abc"
        assert rec.status == "scheduled"
        assert rec.created_at == rec.updated_at


def test_get_missing_returns_none():
    with _store() as s:
        assert s.get("nope") is None


def test_upsert_updates_keep_created_at():
    with _store() as s:
        s.upsert("k1", "yt_abc", "Title", "2026-07-12T14:00:00+00:00", "hash1")
        created = s.get("k1").created_at
        s.upsert("k1", "yt_abc", "New Title", "2026-07-12T15:00:00+00:00", "hash2")
        rec = s.get("k1")
        assert rec.title == "New Title" and rec.content_hash == "hash2"
        assert rec.created_at == created  # preserved across update


def test_active_between_scopes_to_window_and_excludes_cancelled():
    with _store() as s:
        s.upsert("past", "y1", "t", "2026-07-01T00:00:00+00:00", "h")
        s.upsert("inwin", "y2", "t", "2026-07-12T14:00:00+00:00", "h")
        s.upsert("future", "y3", "t", "2026-09-01T00:00:00+00:00", "h")
        s.upsert("cancelled", "y4", "t", "2026-07-13T00:00:00+00:00", "h")
        s.mark_cancelled("cancelled")
        got = s.active_between("2026-07-11T00:00:00+00:00", "2026-07-18T00:00:00+00:00")
        assert [r.key for r in got] == ["inwin"]


def test_mark_cancelled_then_upsert_reactivates():
    with _store() as s:
        s.upsert("k1", "y", "t", "2026-07-12T14:00:00+00:00", "h")
        s.mark_cancelled("k1")
        assert s.get("k1").status == "cancelled"
        s.upsert("k1", "y", "t", "2026-07-12T14:00:00+00:00", "h")
        assert s.get("k1").status == "scheduled"


def test_broadcast_key_merges_sunday_uids_stably():
    start = datetime(2026, 7, 12, 9, 0, tzinfo=CT)
    b1 = Broadcast(Office.ORTHROS_LITURGY, "x", start, None, source_uids=["orthros", "liturgy"])
    b2 = Broadcast(Office.ORTHROS_LITURGY, "x", start, None, source_uids=["liturgy", "orthros"])
    assert b1.key == b2.key  # order-independent
    assert b1.key == "orthros_liturgy|2026-07-12|liturgy,orthros"
