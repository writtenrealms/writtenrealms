from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


ABILITY_INTENT_STATUS_QUEUED = "queued"
ABILITY_INTENT_STATUS_CASTING = "casting"
ABILITY_INTENT_STATUS_CHANNELING = "channeling"
ABILITY_INTENT_TURN_PRIORITY_INTERRUPT = "interrupt"

# Channels do not have an authoring/runtime pipeline yet, but reserving their
# committed status here gives casts and future channels one cancellation
# contract. Queued intents have not started and are deliberately excluded.
INTERRUPTIBLE_ABILITY_INTENT_STATUSES = frozenset(
    {
        ABILITY_INTENT_STATUS_CASTING,
        ABILITY_INTENT_STATUS_CHANNELING,
    }
)


@dataclass(frozen=True)
class InterruptibleAbilityIntent:
    slug: str
    status: str

    @property
    def phase(self) -> str:
        if self.status == ABILITY_INTENT_STATUS_CHANNELING:
            return "channel"
        return "cast"


def ability_intent_is_committed(pending: Any) -> bool:
    if not isinstance(pending, Mapping):
        return False
    status = str(pending.get("status") or "").strip().lower()
    return status in INTERRUPTIBLE_ABILITY_INTENT_STATUSES


def interruptible_ability_intent(
    pending: Any,
) -> InterruptibleAbilityIntent | None:
    if not ability_intent_is_committed(pending):
        return None
    return InterruptibleAbilityIntent(
        slug=str(pending.get("ability") or "").strip().lower(),
        status=str(pending.get("status") or "").strip().lower(),
    )


def ability_intent_turn_priority(ability: Any) -> str | None:
    components = getattr(ability, "components", None)
    if not isinstance(components, list):
        return None
    if any(
        isinstance(component, Mapping)
        and str(component.get("type") or "").strip().lower() == "interrupt"
        for component in components
    ):
        return ABILITY_INTENT_TURN_PRIORITY_INTERRUPT
    return None


def _pending_intent_is_ready_interrupt(pending: Any) -> bool:
    if not isinstance(pending, Mapping):
        return False
    priority = str(pending.get("turn_priority") or "").strip().lower()
    if priority != ABILITY_INTENT_TURN_PRIORITY_INTERRUPT:
        return False
    try:
        rounds_remaining = max(
            0,
            int(pending.get("cast_rounds_remaining") or 0),
        )
    except (TypeError, ValueError):
        rounds_remaining = 0
    return rounds_remaining == 0


def _pending_intent_target_key(pending: Any) -> tuple[str, int] | None:
    if not isinstance(pending, Mapping):
        return None
    target = pending.get("target")
    if not isinstance(target, Mapping):
        return None
    target_type = str(target.get("type") or "").strip().lower()
    try:
        target_id = int(target.get("id") or 0)
    except (TypeError, ValueError):
        return None
    if not target_type or target_id <= 0:
        return None
    return target_type, target_id


def prioritize_ready_interrupts(
    actor_order: Iterable[tuple[str, int]],
    *,
    pending_by_actor: Mapping[tuple[str, int], Any],
) -> list[tuple[str, int]]:
    """Place ready interrupts directly before their committed targets."""
    order = list(actor_order)
    order_set = set(order)
    target_by_interrupter: dict[tuple[str, int], tuple[str, int]] = {}
    for actor_key in order:
        pending = pending_by_actor.get(actor_key)
        if not _pending_intent_is_ready_interrupt(pending):
            continue
        target_key = _pending_intent_target_key(pending)
        if target_key not in order_set:
            continue
        if interruptible_ability_intent(pending_by_actor.get(target_key)) is None:
            continue
        target_by_interrupter[actor_key] = target_key

    if not target_by_interrupter:
        return order

    # A cycle is possible only when committed interrupt abilities target one
    # another. There is no coherent "immediately before" ordering for that
    # group, so preserve its encounter order and apply all other placements.
    cycle_members: set[tuple[str, int]] = set()
    checked: set[tuple[str, int]] = set()
    for actor_key in target_by_interrupter:
        if actor_key in checked:
            continue
        path: list[tuple[str, int]] = []
        path_positions: dict[tuple[str, int], int] = {}
        current = actor_key
        while current in target_by_interrupter and current not in checked:
            if current in path_positions:
                cycle_members.update(path[path_positions[current]:])
                break
            path_positions[current] = len(path)
            path.append(current)
            current = target_by_interrupter[current]
        checked.update(path)

    interrupters_by_target: dict[
        tuple[str, int],
        list[tuple[str, int]],
    ] = {}
    for actor_key in order:
        if actor_key in cycle_members:
            continue
        target_key = target_by_interrupter.get(actor_key)
        if target_key is not None:
            interrupters_by_target.setdefault(target_key, []).append(actor_key)

    derived_order: list[tuple[str, int]] = []
    emitted: set[tuple[str, int]] = set()
    placed_interrupters = set(target_by_interrupter) - cycle_members

    for actor_key in order:
        if actor_key in placed_interrupters or actor_key in emitted:
            continue
        stack = [(actor_key, False)]
        while stack:
            current, children_added = stack.pop()
            if current in emitted:
                continue
            if children_added:
                emitted.add(current)
                derived_order.append(current)
                continue
            stack.append((current, True))
            stack.extend(
                (interrupter_key, False)
                for interrupter_key in reversed(
                    interrupters_by_target.get(current, [])
                )
                if interrupter_key not in emitted
            )
    return derived_order
