"""Liturgical enrichment: tone, season/week, feast, and saints for a given date.

Today this scrapes the server-rendered melkitetypikon.org day page. The Horologion app is
mid-reconfiguration; when a unified JSON API lands, add an ApiTypikonSource with the same
interface and swap it in — nothing else in the pipeline needs to change.
"""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import httpx


@dataclass
class LiturgicalInfo:
    tone: int | None = None
    season: str | None = None
    pent_week: int | None = None
    primary_feast: str | None = None
    saints: list[str] = field(default_factory=list)
    # Populated by the API source (authoritative); left unset by the scraper fallback.
    day_name: str | None = None  # canonical "Nth Sunday after Pentecost" from the engine
    named_days: list[str] = field(default_factory=list)  # e.g. "Sunday of the Prodigal Son"
    vespers_rank: str | None = None  # "great" | "daily" — retires the Great-Vespers guess

    @property
    def sunday_after_pentecost(self) -> str | None:
        if self.season and "pentecost" in self.season.lower() and self.pent_week:
            return f"{_ordinal(self.pent_week)} Sunday after Pentecost"
        return None

    @property
    def sunday_name(self) -> str | None:
        """Best Sunday designation: a named Sunday, else the engine's dayName, else derived."""
        if self.named_days:
            return self.named_days[0]
        return self.day_name or self.sunday_after_pentecost


class TypikonSource(ABC):
    @abstractmethod
    def get(self, day: date) -> LiturgicalInfo: ...


class ScrapeTypikonSource(TypikonSource):
    def __init__(
        self,
        parish: str = "saintgeorge",
        base_url: str = "https://melkitetypikon.org",
        *,
        timeout: float = 30.0,
    ) -> None:
        self.parish = parish
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cache: dict[date, LiturgicalInfo] = {}

    def get(self, day: date) -> LiturgicalInfo:
        if day not in self._cache:
            url = f"{self.base_url}/{day:%Y-%m-%d}?parish={self.parish}"
            resp = httpx.get(url, timeout=self._timeout, follow_redirects=True)
            resp.raise_for_status()
            self._cache[day] = parse_day_html(resp.text)
        return self._cache[day]


class ApiTypikonSource(TypikonSource):
    """Consumes the typikon `/api/liturgical-day/:date` JSON contract.

    Pure-by-date; the caller requests date+1 for Vespers (matching how the API defines
    `vespersRank`). See docs/typikon-api-handoff.md for the contract.
    """

    def __init__(self, base_url: str, *, timeout: float = 5.0) -> None:
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
        pent_week=d.get("weekOfSeason"),
        # feasts are rank-ordered; [] on ordinary days (minor saints live in `saints`).
        primary_feast=(feasts[0]["name"] if feasts else None),
        saints=list(d.get("saints") or []),
        day_name=d.get("dayName"),
        named_days=list(d.get("namedDays") or []),
        vespers_rank=d.get("vespersRank"),
    )


def parse_day_html(page: str) -> LiturgicalInfo:
    return LiturgicalInfo(
        tone=_int(_first(page, r'class="pill tone"[^>]*>\s*Tone\s+(\d+)')),
        season=_first(page, r'class="pill season"[^>]*>\s*([^<]+?)\s*<'),
        # label and value sit in separate spans, so allow markup between them
        pent_week=_int(_first(page, r'Pent\s*Week[^0-9]{0,40}?(\d+)')),
        primary_feast=_parse_primary_feast(page),
        saints=_parse_saints(page),
    )


def _parse_primary_feast(page: str) -> str | None:
    sec = _section(page, "feasts-section")
    if sec is None or re.search(r'class="empty"', sec):
        return None
    items = _list_items(sec)
    return items[0] if items else None


def _parse_saints(page: str) -> list[str]:
    sec = _section(page, "saints-section")
    if sec is None:
        return []
    return _list_items(sec)


def _section(page: str, cls: str) -> str | None:
    m = re.search(rf'<section[^>]*class="[^"]*{cls}[^"]*"[^>]*>(.*?)</section>', page, re.S)
    return m.group(1) if m else None


def _list_items(fragment: str) -> list[str]:
    out: list[str] = []
    for li in re.findall(r"<li[^>]*>(.*?)</li>", fragment, re.S):
        li = re.sub(r'<span[^>]*class="[^"]*badge[^"]*"[^>]*>.*?</span>', "", li, flags=re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", li))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append(text)
    return out


def _first(page: str, pattern: str) -> str | None:
    m = re.search(pattern, page, re.S)
    return html.unescape(m.group(1)).strip() if m else None


def _int(s: str | None) -> int | None:
    return int(s) if s and s.isdigit() else None


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
