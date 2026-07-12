"""YouTube write path: the real BroadcastSink, OAuth helpers, and stream-key resolution.

The API calls go through an injected `service` (a googleapiclient Resource), so the sink is
unit-testable with a mock and needs no credentials until run for real.
"""

from __future__ import annotations

import os

from .plan import PlannedBroadcast
from .sink import BroadcastSink, ExistingBroadcast

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
TOKEN_URI = "https://oauth2.googleapis.com/token"

_TITLE_MAX = 100  # YouTube hard limit
_DESC_MAX = 5000


def _rfc3339(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _clip_title(title: str) -> str:
    if len(title) <= _TITLE_MAX:
        return title
    cut = title[: _TITLE_MAX - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


class YouTubeSink(BroadcastSink):
    def __init__(self, service, *, stream_key: str | None = None, privacy: str = "public") -> None:
        self._svc = service
        self._stream_key = stream_key
        self._stream_id: str | None = None  # resolved lazily, then cached
        self._privacy = privacy

    def _snippet(self, planned: PlannedBroadcast) -> dict:
        return {
            "title": _clip_title(planned.title),
            "description": planned.description[:_DESC_MAX],
            "scheduledStartTime": _rfc3339(planned.start_utc),
        }

    def create(self, planned: PlannedBroadcast) -> str:
        body = {
            "snippet": self._snippet(planned),
            "status": {"privacyStatus": self._privacy, "selfDeclaredMadeForKids": False},
            "contentDetails": {"enableAutoStart": True, "enableAutoStop": True},
        }
        resp = (
            self._svc.liveBroadcasts()
            .insert(part="snippet,status,contentDetails", body=body)
            .execute()
        )
        broadcast_id = resp["id"]
        if self._stream_key:
            self._bind(broadcast_id)
        return broadcast_id

    def update(self, youtube_id: str, planned: PlannedBroadcast) -> None:
        body = {"id": youtube_id, "snippet": self._snippet(planned)}
        self._svc.liveBroadcasts().update(part="snippet", body=body).execute()

    def cancel(self, youtube_id: str) -> None:
        self._svc.liveBroadcasts().delete(id=youtube_id).execute()

    def list_upcoming(self) -> list[ExistingBroadcast]:
        out: list[ExistingBroadcast] = []
        page_token = None
        while True:
            req = self._svc.liveBroadcasts().list(
                part="id,snippet", broadcastStatus="upcoming", maxResults=50,
                pageToken=page_token,
            )
            resp = req.execute()
            for it in resp.get("items", []):
                snip = it.get("snippet", {})
                out.append(
                    ExistingBroadcast(
                        youtube_id=it["id"],
                        title=snip.get("title", ""),
                        start_utc=snip.get("scheduledStartTime", ""),
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    # --- stream binding -----------------------------------------------------------------
    def _bind(self, broadcast_id: str) -> None:
        stream_id = self._resolve_stream_id()
        self._svc.liveBroadcasts().bind(
            id=broadcast_id, part="id,contentDetails", streamId=stream_id
        ).execute()

    def _resolve_stream_id(self) -> str:
        if self._stream_id is None:
            self._stream_id = resolve_stream_id(self._svc, self._stream_key)
        return self._stream_id


def resolve_stream_id(service, stream_key: str) -> str:
    """Find the reusable liveStream whose ingestion key matches `stream_key`."""
    resp = service.liveStreams().list(part="id,cdn", mine=True).execute()
    for item in resp.get("items", []):
        name = item.get("cdn", {}).get("ingestionInfo", {}).get("streamName")
        if name == stream_key:
            return item["id"]
    raise LookupError("No liveStream matches the configured YOUTUBE_STREAM_KEY")


def list_streams(service) -> list[tuple[str, str]]:
    """Return (liveStream id, ingestion stream key) pairs for `mine=True` streams."""
    resp = service.liveStreams().list(part="id,cdn,snippet", mine=True).execute()
    out = []
    for item in resp.get("items", []):
        name = item.get("cdn", {}).get("ingestionInfo", {}).get("streamName", "")
        out.append((item["id"], name))
    return out


# --- credentials / OAuth ----------------------------------------------------------------
def build_service_from_env():
    """Build an authenticated YouTube client from env (refresh token + client creds)."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=_require_env("GOOGLE_OAUTH_REFRESH_TOKEN"),
        client_id=_require_env("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=_require_env("GOOGLE_OAUTH_CLIENT_SECRET"),
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def run_oauth_flow(client_secrets_file: str) -> str:
    """Run the one-time consent (Desktop client) and return the refresh token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
    # select_account forces the chooser so a Brand Account (e.g. the Saint George channel)
    # can be picked instead of the signed-in user's own (possibly empty) channel.
    creds = flow.run_local_server(port=0, prompt="select_account consent")
    if not creds.refresh_token:
        raise RuntimeError("No refresh token returned — revoke prior grant and retry.")
    return creds.refresh_token


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val
