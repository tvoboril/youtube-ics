"""Where assembled broadcasts get written. YouTube is one implementation (added later,
behind OAuth); FakeSink backs the tests and dry-run needs no network."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .plan import PlannedBroadcast


@dataclass
class ExistingBroadcast:
    """A scheduled broadcast already on the channel (used to adopt, not re-create)."""

    youtube_id: str
    title: str
    start_utc: str  # RFC3339; empty for persistent/no-start broadcasts (ignored)


class BroadcastSink(ABC):
    @abstractmethod
    def create(self, planned: PlannedBroadcast) -> str:
        """Create the scheduled live broadcast; return its YouTube broadcast id."""

    @abstractmethod
    def update(self, youtube_id: str, planned: PlannedBroadcast) -> None:
        """Update title/description/scheduled start of an existing broadcast."""

    @abstractmethod
    def cancel(self, youtube_id: str) -> None:
        """Cancel/delete a scheduled broadcast whose calendar event vanished."""

    @abstractmethod
    def list_upcoming(self) -> list[ExistingBroadcast]:
        """Scheduled broadcasts already on the channel — so reconcile can adopt one
        instead of creating a duplicate when the local store is empty/lost."""


@dataclass
class FakeSink(BroadcastSink):
    """Records calls and hands back deterministic ids. For tests and dry-runs."""

    created: list[PlannedBroadcast] = field(default_factory=list)
    updated: list[tuple[str, PlannedBroadcast]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    existing: list[ExistingBroadcast] = field(default_factory=list)
    _n: int = 0

    def create(self, planned: PlannedBroadcast) -> str:
        self._n += 1
        yt = f"fake-yt-{self._n}"
        self.created.append(planned)
        return yt

    def update(self, youtube_id: str, planned: PlannedBroadcast) -> None:
        self.updated.append((youtube_id, planned))

    def cancel(self, youtube_id: str) -> None:
        self.cancelled.append(youtube_id)

    def list_upcoming(self) -> list[ExistingBroadcast]:
        return list(self.existing)
