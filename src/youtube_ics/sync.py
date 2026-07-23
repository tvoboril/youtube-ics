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
    REAP = "reap"  # a past broadcast still stuck 'upcoming' → delete it before autostart grabs it


# How far a broadcast's scheduled start must be in the past before we treat a still-'upcoming'
# broadcast as a dead ghost and delete it. Generous enough to never touch a broadcast scheduled
# earlier *today* that simply hasn't gone live yet (e.g. the encoder starts late).
REAP_GRACE = timedelta(hours=12)


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
    youtube_id: str | None = None  # for UPDATE/CANCEL/ADOPT
    existing_title: str | None = None  # for ADOPT: the channel's current title (preserved)


def plan_actions(
    plan: list[PlannedBroadcast],
    store: Store,
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
    existing: list[ExistingBroadcast] | None = None,
) -> list[Action]:
    """Decide actions against the *channel's* actual state (``existing``), not the store alone.

    Three properties this guarantees:

    * **Self-heal.** A tracked broadcast that has vanished from the channel (deleted in
      Studio, or lost to a bug) is re-created rather than reported as unchanged forever.
    * **Operator owns the title.** Once a broadcast exists, a title edited in YouTube Studio
      is never reverted and never triggers a re-create. We only push a title update while the
      channel title still matches what we last wrote (i.e. the operator hasn't touched it).
    * **No cross-key cancel.** The vanish-scan never cancels a ``youtube_id`` that a surviving
      planned key still points at. When a day's office reshapes (e.g. a standalone Orthros
      becomes a merged Orthros + Divine Liturgy) the new key adopts the same broadcast, and
      the old key's disappearance must not delete it out from under the new one.

    Vanish-scan stays scoped to the planning window so past/over-the-horizon rows are left be.
    """
    actions: list[Action] = []
    planned_keys: set[str] = set()
    # index existing channel broadcasts by id and by scheduled start instant
    existing_by_id: dict[str, ExistingBroadcast] = {}
    by_instant: dict[float, ExistingBroadcast] = {}
    for eb in existing or []:
        existing_by_id[eb.youtube_id] = eb
        inst = _instant(eb.start_utc)
        if inst is not None:
            by_instant.setdefault(inst, eb)

    # youtube_ids that a surviving planned key keeps alive — protected from the vanish-scan.
    kept_ids: set[str] = set()

    def _adopt_or_create(p: PlannedBroadcast) -> None:
        """No live broadcast tracked for this key: reuse one already at this slot, else create."""
        slot = by_instant.get(_instant(p.start_utc.isoformat()))
        if slot is None:
            actions.append(Action(ActionKind.CREATE, p.key, planned=p))
            return
        prior = store.get_by_youtube_id(slot.youtube_id)
        if prior is not None and prior.title == slot.title:
            # A broadcast *we* created, now re-keyed by an office reshape (e.g. a standalone
            # Orthros that merged into Orthros + Divine Liturgy), and the channel title is still
            # what we last wrote (the operator hasn't renamed it). Re-point the new key at the
            # broadcast AND push the plan, so its title/description reflect the reshaped office.
            actions.append(Action(ActionKind.UPDATE, p.key, planned=p, youtube_id=slot.youtube_id))
        else:
            # Store loss, or a broadcast we don't track (title may be operator-set): record the
            # mapping and preserve the channel's current title rather than overwriting it.
            actions.append(
                Action(ActionKind.ADOPT, p.key, planned=p, youtube_id=slot.youtube_id,
                       existing_title=slot.title)
            )
        kept_ids.add(slot.youtube_id)

    for p in plan:
        planned_keys.add(p.key)
        rec = store.get(p.key)
        if rec is None or rec.status == "cancelled":
            _adopt_or_create(p)
            continue
        eb = existing_by_id.get(rec.youtube_id)
        if eb is None:
            # Tracked, but gone from the channel — self-heal (re-adopt the slot or re-create).
            _adopt_or_create(p)
        elif eb.title != rec.title:
            # Operator renamed it in Studio → hands off: never revert, never re-create.
            actions.append(Action(ActionKind.NOOP, p.key, planned=p, youtube_id=rec.youtube_id))
            kept_ids.add(rec.youtube_id)
        elif rec.content_hash != p.content_hash:
            actions.append(Action(ActionKind.UPDATE, p.key, planned=p, youtube_id=rec.youtube_id))
            kept_ids.add(rec.youtube_id)
        else:
            actions.append(Action(ActionKind.NOOP, p.key, planned=p, youtube_id=rec.youtube_id))
            kept_ids.add(rec.youtube_id)

    # Anything scheduled within the window that is no longer in the plan has vanished — cancel
    # it, unless a surviving planned key still points at the same broadcast (shared adoption).
    for rec in store.active_between(window_start_utc.isoformat(), window_end_utc.isoformat()):
        if rec.key not in planned_keys and rec.youtube_id not in kept_ids:
            actions.append(Action(ActionKind.CANCEL, rec.key, youtube_id=rec.youtube_id))

    # Reap dead ghosts: broadcasts still stuck 'upcoming' on the channel whose scheduled start
    # is well in the past. A broadcast that actually streamed transitions to 'complete' and drops
    # off list_upcoming; a live one is 'active' and also absent — so anything left here with a
    # past start never went live. Because every broadcast is bound to the one reusable stream with
    # autostart, such a ghost would be silently transitioned live the next time the encoder
    # connects (this is exactly how a past date "fired a second time"). Delete it.
    reap_before = (window_start_utc - REAP_GRACE).timestamp()
    for eb in existing or []:
        inst = _instant(eb.start_utc)
        if inst is None or inst >= reap_before:
            continue  # persistent/no-start broadcast, or recent/future — leave it
        if eb.youtube_id in kept_ids:
            continue  # a surviving planned key still depends on it
        actions.append(Action(ActionKind.REAP, eb.youtube_id, youtube_id=eb.youtube_id))
    return actions


def apply_action(action: Action, store: Store, sink: BroadcastSink) -> None:
    if action.kind is ActionKind.CREATE:
        p = action.planned
        yt = sink.create(p)
        store.upsert(p.key, yt, p.title, p.start_utc.isoformat(), p.content_hash)
    elif action.kind is ActionKind.ADOPT:
        # Already on the channel: record the mapping only, no API call. Store the channel's
        # current title (not the plan's) so a later operator rename is detected as a drift.
        p = action.planned
        title = action.existing_title if action.existing_title is not None else p.title
        store.upsert(p.key, action.youtube_id, title, p.start_utc.isoformat(), p.content_hash)
    elif action.kind is ActionKind.UPDATE:
        p = action.planned
        sink.update(action.youtube_id, p)
        store.upsert(p.key, action.youtube_id, p.title, p.start_utc.isoformat(), p.content_hash)
    elif action.kind is ActionKind.CANCEL:
        sink.cancel(action.youtube_id)
        store.mark_cancelled(action.key)
    elif action.kind is ActionKind.REAP:
        # Delete the stale broadcast from the channel; if we happen to track it, mark it cancelled
        # so the store stops advertising it as live.
        sink.cancel(action.youtube_id)
        tracked = store.get_by_youtube_id(action.youtube_id)
        if tracked is not None:
            store.mark_cancelled(tracked.key)
    # NOOP: nothing to do


@dataclass
class ReconcileSummary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    cancelled: int = 0
    adopted: int = 0
    reaped: int = 0
    actions: list[Action] = field(default_factory=list)


_COUNT_FIELD = {
    ActionKind.CREATE: "created",
    ActionKind.UPDATE: "updated",
    ActionKind.NOOP: "unchanged",
    ActionKind.CANCEL: "cancelled",
    ActionKind.ADOPT: "adopted",
    ActionKind.REAP: "reaped",
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
