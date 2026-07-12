"""Runtime configuration, sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

SAINTGEORGE_CALENDAR_ID = (
    "c_863517ec86ee71218e73780e73667efa25c4f5818b26bb7a4fcae8ce076d860d@group.calendar.google.com"
)


def default_ics_url() -> str:
    from urllib.parse import quote

    cal_id = os.environ.get("ICS_CALENDAR_ID", SAINTGEORGE_CALENDAR_ID)
    return f"https://calendar.google.com/calendar/ical/{quote(cal_id)}/public/basic.ics"


@dataclass
class Config:
    ics_url: str
    lookahead_days: int  # schedule at most this far out (1 week default, 2 max)
    parish: str
    typikon_base_url: str  # scraper fallback (melkitetypikon.org HTML)
    typikon_api_url: str | None  # if set, use the JSON API instead of scraping
    stream_key: str | None  # ATEM RTMP key -> resolved to a liveStream id for bind
    db_path: str  # SQLite state file

    @classmethod
    def from_env(cls) -> "Config":
        days = int(os.environ.get("LOOKAHEAD_DAYS", "14"))
        days = max(1, min(days, 14))  # hard cap at 2 weeks — never schedule further out
        return cls(
            ics_url=os.environ.get("ICS_URL", default_ics_url()),
            lookahead_days=days,
            parish=os.environ.get("PARISH", "saintgeorge"),
            typikon_base_url=os.environ.get("TYPIKON_BASE_URL", "https://melkitetypikon.org"),
            typikon_api_url=os.environ.get("TYPIKON_API_URL"),
            stream_key=os.environ.get("YOUTUBE_STREAM_KEY"),
            db_path=os.environ.get("DB_PATH", "youtube_ics.sqlite"),
        )
