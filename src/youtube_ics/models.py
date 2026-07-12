"""Core data types shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum


class Office(str, Enum):
    """The kind of service a broadcast covers, used to pick the title prefix."""

    ORTHROS_LITURGY = "orthros_liturgy"  # Sunday merged Orthros + Divine Liturgy
    ORTHROS = "orthros"
    LITURGY = "liturgy"
    VESPERS = "vespers"
    OTHER = "other"  # any other tagged service (Paraklisis, Akathist, ...): name + date only


@dataclass
class Occurrence:
    """One concrete (RRULE-expanded) calendar instance that passed the filter."""

    uid: str
    summary: str
    start: datetime  # timezone-aware
    end: datetime | None
    is_recurring: bool  # came from an RRULE master (a generic weekly service)

    @property
    def local_date(self) -> date:
        return self.start.date()


@dataclass
class Broadcast:
    """An assembled YouTube broadcast to create (post-merge, post-supersede)."""

    office: Office
    office_label: str  # e.g. "Great Vespers", "Divine Liturgy"
    start: datetime  # timezone-aware
    end: datetime | None
    source_uids: list[str] = field(default_factory=list)
    from_feast: bool = False  # a specific feast event (vs generic recurring)

    @property
    def local_date(self) -> date:
        return self.start.date()

    @property
    def is_sunday_combo(self) -> bool:
        return self.office is Office.ORTHROS_LITURGY

    @property
    def key(self) -> str:
        """Stable identity across syncs. Includes the date (recurring instances share a
        master UID) and all source UIDs (so a merged Sunday maps to one row)."""
        uids = ",".join(sorted(self.source_uids))
        return f"{self.office.value}|{self.local_date.isoformat()}|{uids}"

    def commemoration_date(self) -> date:
        """Which liturgical day supplies the commemoration.

        Vespers begins the next liturgical day, so it takes the FOLLOWING calendar
        day's feast/saint. Everything else uses its own date.
        """
        if self.office is Office.VESPERS:
            return (self.start + _ONE_DAY).date()
        return self.local_date


from datetime import timedelta as _timedelta  # noqa: E402

_ONE_DAY = _timedelta(days=1)
