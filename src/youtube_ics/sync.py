"""Reconcile the desired plan against stored state: create / update / cancel.

Split into pure `plan_actions()` (decides what to do) and `apply_action()` (does it), so a
dry run can print the exact actions without touching YouTube or the store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from .plan import PlannedBroadcast
from .sink import BroadcastSink, ExistingBroadcast
from .store import Store


class ActionKind(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    NOOP = "noop"
    CANCEL = "cancel"
    ADOPT = "adopt"  # store was empty/lost but the broadcast already exists → record it


def _instant(iso: str) -> float | None:
    """Parse an RFC3339 timestamp to a comparable UTC instant (seconds), or None."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Action:
    kind: ActionKind
    key: str
    planned: PlannedBroadcast | None = None  # for CREATE/UPDATE/NOOP
    youtube_id: str | None = None  # for UPDATE/CANCEL


def plan_actions(
    plan: list[PlannedBroadcast],
    store: Store,
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
    existing: list[ExistingBroadcast] | None = None,
) -> list[Action]:
    """Decide actions. Vanish-scan is scoped to the planning window so we never cancel past
    broadcasts or ones scheduled beyond the horizon. When the store has no row for a planned
    broadcast, adopt a channel broadcast at the same start instant instead of creating a
    duplicate (self-heals a lost/empty store)."""
    actions: list[Action] = []
    planned_keys: set[str] = set()
    # index existing channel broadcasts by their scheduled start instant
    by_instant: dict[float, ExistingBroadcast] = {}
    for eb in existing or []:
        inst = _instant(eb.start_utc)
        if inst is not None:
            by_instant.setdefault(inst, eb)

    for p in plan:
        planned_keys.add(p.key)
        rec = store.get(p.key)
        if rec is None or rec.status == "cancelled":
            match = by_instant.get(_instant(p.start_utc.isoformat()))
            if match is not None:
                # broadcast already exists on the channel — adopt it, don't create.
                if match.title == p.title:
                    actions.append(Action(ActionKind.ADOPT, p.key, planned=p, youtube_id=match.youtube_id))
                else:
                    actions.append(Action(ActionKind.UPDATE, p.key, planned=p, youtube_id=match.youtube_id))
            else:
                actions.append(Action(ActionKind.CREATE, p.key, planned=p))
        elif rec.content_hash != p.content_hash:
            actions.append(Action(ActionKind.UPDATE, p.key, planned=p, youtube_id=rec.youtube_id))
        else:
            actions.append(Action(ActionKind.NOOP, p.key, planned=p, youtube_id=rec.youtube_id))

    # Anything scheduled within the window that is no longer in the plan has vanished.
    for rec in store.active_between(window_start_utc.isoformat(), window_end_utc.isoformat()):
        if rec.key not in planned_keys:
            actions.append(Action(ActionKind.CANCEL, rec.key, youtube_id=rec.youtube_id))
    return actions


def apply_action(action: Action, store: Store, sink: BroadcastSink) -> None:
    if action.kind is ActionKind.CREATE:
        p = action.planned
        yt = sink.create(p)
        store.upsert(p.key, yt, p.title, p.start_utc.isoformat(), p.content_hash)
    elif action.kind is ActionKind.ADOPT:
        # already on the channel + content matches: just record the mapping, no API call.
        p = action.planned
        store.upsert(p.key, action.youtube_id, p.title, p.start_utc.isoformat(), p.content_hash)
    elif action.kind is ActionKind.UPDATE:
        p = action.planned
        sink.update(action.youtube_id, p)
        store.upsert(p.key, action.youtube_id, p.title, p.start_utc.isoformat(), p.content_hash)
    elif action.kind is ActionKind.CANCEL:
        sink.cancel(action.youtube_id)
        store.mark_cancelled(action.key)
    # NOOP: nothing to do


@dataclass
class ReconcileSummary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    cancelled: int = 0
    adopted: int = 0
    actions: list[Action] = field(default_factory=list)


_COUNT_FIELD = {
    ActionKind.CREATE: "created",
    ActionKind.UPDATE: "updated",
    ActionKind.NOOP: "unchanged",
    ActionKind.CANCEL: "cancelled",
    ActionKind.ADOPT: "adopted",
}


def reconcile(
    plan: list[PlannedBroadcast],
    store: Store,
    sink: BroadcastSink,
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
    dry_run: bool = False,
) -> ReconcileSummary:
    actions = plan_actions(
        plan, store, window_start_utc=window_start_utc, window_end_utc=window_end_utc,
        existing=sink.list_upcoming(),
    )
    summary = ReconcileSummary(actions=actions)
    for a in actions:
        setattr(summary, _COUNT_FIELD[a.kind], getattr(summary, _COUNT_FIELD[a.kind]) + 1)
        if not dry_run:
            apply_action(a, store, sink)
    return summary
