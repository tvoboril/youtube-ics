"""SQLite state: maps a broadcast identity key -> the YouTube broadcast it created.

The reconcile loop uses this to decide, per sync: create (key unseen), update
(content_hash changed), or cancel (a stored future broadcast that vanished from the plan).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS broadcasts (
    key           TEXT PRIMARY KEY,
    youtube_id    TEXT NOT NULL,
    title         TEXT NOT NULL,
    start_utc     TEXT NOT NULL,   -- ISO8601, UTC; enables range queries for vanish-scan
    content_hash  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled | cancelled
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_broadcasts_start ON broadcasts(start_utc);
"""


@dataclass
class Record:
    key: str
    youtube_id: str
    title: str
    start_utc: str
    content_hash: str
    status: str
    created_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str = "youtube_ics.sqlite") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, key: str) -> Record | None:
        with closing(self._conn.execute("SELECT * FROM broadcasts WHERE key = ?", (key,))) as cur:
            row = cur.fetchone()
        return _record(row) if row else None

    def get_by_youtube_id(self, youtube_id: str) -> Record | None:
        """The scheduled row we last wrote for this broadcast, regardless of key.

        Lets reconcile tell "a broadcast we created, now re-keyed by an office reshape" (this
        returns a row) from "a broadcast we don't track / an operator's" (returns None).
        """
        with closing(
            self._conn.execute(
                """
                SELECT * FROM broadcasts
                WHERE youtube_id = ? AND status = 'scheduled'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (youtube_id,),
            )
        ) as cur:
            row = cur.fetchone()
        return _record(row) if row else None

    def upsert(self, key: str, youtube_id: str, title: str, start_utc: str, content_hash: str) -> None:
        """Insert a new mapping or update an existing one (keeping created_at)."""
        now = _now_iso()
        self._conn.execute(
            """
            INSERT INTO broadcasts (key, youtube_id, title, start_utc, content_hash,
                                    status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                youtube_id   = excluded.youtube_id,
                title        = excluded.title,
                start_utc    = excluded.start_utc,
                content_hash = excluded.content_hash,
                status       = 'scheduled',
                updated_at   = excluded.updated_at
            """,
            (key, youtube_id, title, start_utc, content_hash, now, now),
        )
        self._conn.commit()

    def mark_cancelled(self, key: str) -> None:
        self._conn.execute(
            "UPDATE broadcasts SET status = 'cancelled', updated_at = ? WHERE key = ?",
            (_now_iso(), key),
        )
        self._conn.commit()

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM broadcasts WHERE key = ?", (key,))
        self._conn.commit()

    def active_between(self, start_utc: str, end_utc: str) -> list[Record]:
        """Scheduled (non-cancelled) broadcasts whose start falls in [start, end).

        Used to find rows that vanished from the plan within the current window so they can
        be cancelled — without touching past broadcasts or ones beyond the horizon.
        """
        with closing(
            self._conn.execute(
                """
                SELECT * FROM broadcasts
                WHERE status = 'scheduled' AND start_utc >= ? AND start_utc < ?
                ORDER BY start_utc
                """,
                (start_utc, end_utc),
            )
        ) as cur:
            return [_record(r) for r in cur.fetchall()]


def _record(row: sqlite3.Row) -> Record:
    return Record(
        key=row["key"],
        youtube_id=row["youtube_id"],
        title=row["title"],
        start_utc=row["start_utc"],
        content_hash=row["content_hash"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
