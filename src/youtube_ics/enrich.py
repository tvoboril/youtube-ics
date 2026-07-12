"""Liturgical enrichment: tone, season/week, feast, saints, and Vespers rank for a date.

Sourced from the melkite-typikon `/api/liturgical-day/:date` JSON API. The endpoint is
pure-by-date; because Vespers opens the *next* liturgical day, the caller requests date+1
for Vespers (which is how the API defines `vespersRank`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import httpx


@dataclass
class LiturgicalInfo:
    tone: int | None = None
    season: str | None = None
    week_of_season: int | None = None
    primary_feast: str | None = None  # highest-rank feast, or None on ordinary days
    saints: list[str] = field(default_factory=list)
    day_name: str | None = None  # canonical "Nth Sunday after Pentecost" from the engine
    named_days: list[str] = field(default_factory=list)  # e.g. "Sunday of the Prodigal Son"
    vespers_rank: str | None = None  # "great" | "daily"

    @property
    def sunday_name(self) -> str | None:
        """Best Sunday designation: a named Sunday, else the engine's dayName, else a
        derived 'Nth Sunday after Pentecost' from the season + week."""
        if self.named_days:
            return self.named_days[0]
        if self.day_name:
            return self.day_name
        if self.season and "pentecost" in self.season.lower() and self.week_of_season:
            return f"{_ordinal(self.week_of_season)} Sunday after Pentecost"
        return None


class TypikonSource(ABC):
    @abstractmethod
    def get(self, day: date) -> LiturgicalInfo: ...


class ApiTypikonSource(TypikonSource):
    """Consumes the typikon `/api/liturgical-day/:date` JSON contract (results cached)."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cache: dict[date, LiturgicalInfo] = {}

    def get(self, day: date) -> LiturgicalInfo:
        if day not in self._cache:
            url = f"{self.base_url}/api/liturgical-day/{day.isoformat()}"
            resp = httpx.get(url, timeout=self._timeout, follow_redirects=True)
            resp.raise_for_status()
            self._cache[day] = liturgical_info_from_api(resp.json())
        return self._cache[day]


def liturgical_info_from_api(d: dict) -> LiturgicalInfo:
    feasts = d.get("feasts") or []
    return LiturgicalInfo(
        tone=d.get("tone"),
        season=d.get("season"),
        week_of_season=d.get("weekOfSeason"),
        # feasts are rank-ordered; [] on ordinary days (minor saints live in `saints`).
        primary_feast=(feasts[0]["name"] if feasts else None),
        saints=list(d.get("saints") or []),
        day_name=d.get("dayName"),
        named_days=list(d.get("namedDays") or []),
        vespers_rank=d.get("vespersRank"),
    )


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
