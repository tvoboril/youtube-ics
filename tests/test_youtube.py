from datetime import datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from youtube_ics.models import Broadcast, Office
from youtube_ics.plan import PlannedBroadcast
from youtube_ics.youtube import YouTubeSink, list_streams, resolve_stream_id, _clip_title

CT = ZoneInfo("America/Chicago")


def _pb(title="Divine Liturgy - Feast - 6 August 2026", desc="body"):
    start = datetime(2026, 8, 6, 18, 0, tzinfo=CT)
    bc = Broadcast(Office.LITURGY, "Divine Liturgy", start, start + timedelta(hours=1))
    return PlannedBroadcast(broadcast=bc, title=title, description=desc)


def _service_with_stream(key="KEY123"):
    svc = MagicMock()
    svc.liveBroadcasts.return_value.insert.return_value.execute.return_value = {"id": "bid1"}
    svc.liveStreams.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "sid9", "cdn": {"ingestionInfo": {"streamName": key}}}]
    }
    return svc


def test_create_inserts_public_and_binds_stream():
    svc = _service_with_stream("KEY123")
    sink = YouTubeSink(svc, stream_key="KEY123")
    yt = sink.create(_pb())
    assert yt == "bid1"
    insert = svc.liveBroadcasts.return_value.insert
    part = insert.call_args.kwargs["part"]
    body = insert.call_args.kwargs["body"]
    assert "snippet" in part and "status" in part
    assert body["status"]["privacyStatus"] == "public"
    assert body["snippet"]["scheduledStartTime"].endswith("Z")  # RFC3339 UTC
    # bound to the resolved stream id
    bind = svc.liveBroadcasts.return_value.bind
    assert bind.call_args.kwargs["streamId"] == "sid9"


def test_create_without_stream_key_does_not_bind():
    svc = MagicMock()
    svc.liveBroadcasts.return_value.insert.return_value.execute.return_value = {"id": "b2"}
    sink = YouTubeSink(svc, stream_key=None)
    sink.create(_pb())
    svc.liveBroadcasts.return_value.bind.assert_not_called()


def test_stream_id_resolved_once_and_cached():
    svc = _service_with_stream("KEY123")
    sink = YouTubeSink(svc, stream_key="KEY123")
    sink.create(_pb())
    sink.create(_pb())
    assert svc.liveStreams.return_value.list.call_count == 1  # cached after first


def test_update_and_cancel():
    svc = MagicMock()
    sink = YouTubeSink(svc, stream_key=None)
    sink.update("bidX", _pb(title="New Title"))
    ub = svc.liveBroadcasts.return_value.update.call_args.kwargs["body"]
    assert ub["id"] == "bidX" and ub["snippet"]["title"] == "New Title"
    sink.cancel("bidX")
    assert svc.liveBroadcasts.return_value.delete.call_args.kwargs["id"] == "bidX"


def test_resolve_stream_id_no_match_raises():
    svc = _service_with_stream("OTHER")
    with pytest.raises(LookupError):
        resolve_stream_id(svc, "KEY123")


def test_list_streams_returns_id_key_pairs():
    svc = MagicMock()
    svc.liveStreams.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "s1", "cdn": {"ingestionInfo": {"streamName": "k1"}}},
            {"id": "s2", "cdn": {"ingestionInfo": {"streamName": "k2"}}},
        ]
    }
    assert list_streams(svc) == [("s1", "k1"), ("s2", "k2")]


def test_title_clipped_to_100_on_word_boundary():
    long = "Divine Liturgy - " + "Very Holy Martyr " * 12 + "of Somewhere - 6 August 2026"
    clipped = _clip_title(long)
    assert len(clipped) <= 100
    assert clipped.endswith("…")
    assert "  " not in clipped  # no dangling partial word artifacts
