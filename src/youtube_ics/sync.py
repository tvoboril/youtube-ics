"""Reconcile the desired plan against stored state: create / update / cancel.

Split into pure `plan_actions()` (decides what to do) and `apply_action()` (does it), so a
dry run can print the exact actions without touching YouTube or the store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from .plan import PlannedBroadcast
from .sink import BroadcastSink
from .store import Store


class ActionKind(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    NOOP = "noop"
    CANCEL = "cancel"


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
) -> list[Action]:
    """Decide actions. Vanish-scan is scoped to the planning window so we never cancel past
    broadcasts or ones scheduled beyond the horizon."""
    actions: list[Action] = []
    planned_keys: set[str] = set()

    for p in plan:
        planned_keys.add(p.key)
        rec = store.get(p.key)
        if rec is None or rec.status == "cancelled":
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
    actions: list[Action] = field(default_factory=list)


_COUNT_FIELD = {
    ActionKind.CREATE: "created",
    ActionKind.UPDATE: "updated",
    ActionKind.NOOP: "unchanged",
    ActionKind.CANCEL: "cancelled",
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
        plan, store, window_start_utc=window_start_utc, window_end_utc=window_end_utc
    )
    summary = ReconcileSummary(actions=actions)
    for a in actions:
        setattr(summary, _COUNT_FIELD[a.kind], getattr(summary, _COUNT_FIELD[a.kind]) + 1)
        if not dry_run:
            apply_action(a, store, sink)
    return summary
