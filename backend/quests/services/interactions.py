from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from typing import Any

from core.utils import format_actor_msg
from quests.entity_refs import resolve_template_ref_id
from quests.services.discovery import (
    available_room_prompt_opportunities_for_room,
    available_npc_dialogue_opportunities_for_mob_template,
    available_npc_dialogue_opportunities_for_room_mobs,
    room_prompt_callouts_for_room,
)
from quests.services.engine import active_instances_qs, get_step
from quests.services.predicates import evaluate_condition
from spawns.events import GameEvent


def _objective_specs(step: dict[str, Any]) -> list[dict[str, Any]]:
    return [objective for objective in (step.get("objectives") or []) if isinstance(objective, dict)]


def _objective_target(objective_spec: dict[str, Any]) -> int:
    progress_spec = objective_spec.get("progress") or {}
    try:
        target = int(progress_spec.get("target") or 1)
    except (TypeError, ValueError):
        target = 1
    return max(target, 1)


def _event_target_template_id(event_data: dict[str, Any] | None) -> int | None:
    if not isinstance(event_data, dict):
        return None
    target = event_data.get("target")
    if not isinstance(target, dict):
        return None
    try:
        return int(target.get("template_id") or 0) or None
    except (TypeError, ValueError):
        return None


def _event_target_room_id(event_data: dict[str, Any] | None) -> int | None:
    if not isinstance(event_data, dict):
        return None
    if str(event_data.get("target_type") or "").strip().lower() != "room":
        return None
    target = event_data.get("target")
    if not isinstance(target, dict):
        return None
    try:
        return int(target.get("id") or 0) or None
    except (TypeError, ValueError):
        return None


def _mob_selector_from_target(target_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(target_payload, dict):
        return None
    keywords = str(target_payload.get("keywords") or "").strip().lower()
    if keywords:
        tokens = keywords.split()
        return tokens[-1]
    name = str(target_payload.get("name") or "").strip().lower()
    if name:
        return name.split()[-1]
    return None


def _presented_opportunity_events(player, opportunities: list[dict[str, Any]]) -> list[GameEvent]:
    if not opportunities:
        return []

    blocks: list[str] = []
    for opportunity in opportunities:
        title = str(opportunity.get("name") or opportunity.get("slug") or "Quest").strip()
        body = str(((opportunity.get("text") or {}).get("body")) or "").strip()
        recap = str(opportunity.get("recap") or "").strip()
        lines = [f"Quest available: {title}"]
        if body:
            lines.append(body)
        elif recap:
            lines.append(recap)
        lines.append(f"Accept with: quest accept {opportunity.get('slug')}")
        blocks.append("\n".join(lines))

    return [
        GameEvent(
            type="quest.opportunity.presented",
            recipients=[player.key],
            data={"opportunities": opportunities},
            text="\n\n".join(blocks),
        )
    ]


def _inventory_template_counts(player) -> Counter:
    counts: Counter = Counter()
    for item in player.inventory.all():
        if getattr(item, "is_pending_deletion", False):
            continue
        template_id = getattr(item, "template_id", None)
        if template_id:
            counts[int(template_id)] += 1
    return counts


def _preview_state_map(quest_instance) -> dict[str, SimpleNamespace]:
    return {
        state.objective_id: SimpleNamespace(
            status=state.status,
            progress_current=int(state.progress_current or 0),
            progress_target=int(state.progress_target or 0),
        )
        for state in quest_instance.objective_states.all()
    }


def _mark_objective_complete(
    preview_state_map: dict[str, SimpleNamespace],
    objective_spec: dict[str, Any],
) -> None:
    objective_id = str(objective_spec.get("id") or "").strip()
    if not objective_id:
        return
    state = preview_state_map.get(objective_id)
    if state is None:
        return
    state.progress_current = _objective_target(objective_spec)
    state.progress_target = _objective_target(objective_spec)
    state.status = "complete"


def _resolve_condition_template_value(
    quest_instance,
    path: str,
    value: Any,
    *,
    player,
) -> Any:
    if path != "event.target.template_id":
        return value
    resolved = resolve_template_ref_id(
        world=getattr(quest_instance.template, "world", None) or getattr(player, "world", None),
        value=value,
        expected_type="mobtemplate",
    )
    if resolved is not None:
        return resolved
    return value


def _condition_targets_mob_template(
    condition: Any,
    quest_instance,
    *,
    player,
    mob_template_id: int,
) -> bool:
    if condition in (None, {}, []):
        return False
    if isinstance(condition, list):
        return any(
            _condition_targets_mob_template(
                item,
                quest_instance,
                player=player,
                mob_template_id=mob_template_id,
            )
            for item in condition
        )
    if not isinstance(condition, dict):
        return False
    if "all" in condition:
        return any(
            _condition_targets_mob_template(
                item,
                quest_instance,
                player=player,
                mob_template_id=mob_template_id,
            )
            for item in condition.get("all") or []
        )
    if "any" in condition:
        return any(
            _condition_targets_mob_template(
                item,
                quest_instance,
                player=player,
                mob_template_id=mob_template_id,
            )
            for item in condition.get("any") or []
        )
    if "not" in condition:
        return False
    for operator in ("eq", "in"):
        if operator not in condition:
            continue
        raw_args = condition.get(operator) or []
        if not isinstance(raw_args, (list, tuple)) or len(raw_args) != 2:
            continue
        path = str(raw_args[0] or "").strip()
        if path != "event.target.template_id":
            continue
        raw_value = raw_args[1]
        if operator == "eq":
            return _resolve_condition_template_value(
                quest_instance,
                path,
                raw_value,
                player=player,
            ) == mob_template_id
        if not isinstance(raw_value, (list, tuple, set)):
            return False
        return any(
            _resolve_condition_template_value(
                quest_instance,
                path,
                candidate,
                player=player,
            ) == mob_template_id
            for candidate in raw_value
        )
    return False


def _step_transitions_fire(
    quest_instance,
    step: dict[str, Any],
    *,
    player,
    preview_state_map: dict[str, SimpleNamespace],
    event_data: dict[str, Any] | None = None,
) -> bool:
    transitions = [transition for transition in (step.get("transitions") or []) if isinstance(transition, dict)]
    for transition in transitions:
        if evaluate_condition(
            transition.get("when"),
            player=player,
            template=quest_instance.template,
            quest_instance=quest_instance,
            event_data=event_data,
            objective_state_map=preview_state_map,
        ):
            return True
    return False


def _preview_talk_completion(
    quest_instance,
    step: dict[str, Any],
    *,
    player,
    mob_template_id: int,
) -> bool:
    preview_state_map = _preview_state_map(quest_instance)
    talk_event_data = {"target": {"template_id": mob_template_id}}
    changed = False

    for objective_spec in _objective_specs(step):
        objective_id = str(objective_spec.get("id") or "").strip()
        state = preview_state_map.get(objective_id)
        if state is None or state.status == "complete":
            continue
        tracker = objective_spec.get("tracker") or {}
        if str(tracker.get("event") or "").strip().lower() != "cmd.talk.success":
            continue
        if not evaluate_condition(
            tracker.get("where"),
            player=player,
            template=quest_instance.template,
            quest_instance=quest_instance,
            event_data=talk_event_data,
            objective_state_map=preview_state_map,
        ):
            continue
        _mark_objective_complete(preview_state_map, objective_spec)
        changed = True

    if not changed:
        return False
    return _step_transitions_fire(
        quest_instance,
        step,
        player=player,
        preview_state_map=preview_state_map,
        event_data=talk_event_data,
    )


def _preview_delivery_completion(
    quest_instance,
    step: dict[str, Any],
    *,
    player,
    mob_template_id: int,
    inventory_counts: Counter,
) -> bool:
    preview_state_map = _preview_state_map(quest_instance)
    remaining_inventory = Counter(inventory_counts)
    changed = False

    for objective_spec in _objective_specs(step):
        objective_id = str(objective_spec.get("id") or "").strip()
        state = preview_state_map.get(objective_id)
        if state is None or state.status == "complete":
            continue
        tracker = objective_spec.get("tracker") or {}
        if str(tracker.get("event") or "").strip().lower() != "quest.item.delivered":
            continue

        required_count = max(
            _objective_target(objective_spec) - int(state.progress_current or 0),
            0,
        )
        if required_count <= 0:
            continue

        matching_template_id = None
        for item_template_id, available_count in remaining_inventory.items():
            if available_count < required_count:
                continue
            delivery_event_data = {
                "target": {"template_id": mob_template_id},
                "item": {"template_id": item_template_id},
            }
            if evaluate_condition(
                tracker.get("where"),
                player=player,
                template=quest_instance.template,
                quest_instance=quest_instance,
                event_data=delivery_event_data,
                objective_state_map=preview_state_map,
            ):
                matching_template_id = item_template_id
                break

        if matching_template_id is None:
            continue

        remaining_inventory[matching_template_id] -= required_count
        _mark_objective_complete(preview_state_map, objective_spec)
        changed = True

    if not changed:
        return False
    return _step_transitions_fire(
        quest_instance,
        step,
        player=player,
        preview_state_map=preview_state_map,
    )


def _active_item_turn_in_hint(
    player,
    *,
    mob_template_id: int,
    target_payload: dict[str, Any] | None = None,
) -> str | None:
    inventory_counts = _inventory_template_counts(player)
    selector = _mob_selector_from_target(target_payload)

    for quest_instance in active_instances_qs(player):
        step = get_step(quest_instance.template, quest_instance.current_step_id)
        if not step or str(step.get("kind") or "").strip().lower() != "objective":
            continue

        has_delivery_objective = False
        for objective_spec in _objective_specs(step):
            tracker = objective_spec.get("tracker") or {}
            if str(tracker.get("event") or "").strip().lower() != "quest.item.delivered":
                continue
            if _condition_targets_mob_template(
                tracker.get("where"),
                quest_instance,
                player=player,
                mob_template_id=mob_template_id,
            ):
                has_delivery_objective = True
                break
        if not has_delivery_objective:
            continue

        objective_lines: list[str] = []
        for state in quest_instance.objective_states.all():
            if state.status == "hidden":
                continue
            label = format_actor_msg(
                state.text or state.objective_id,
                player,
                character=player,
                quest_instance=quest_instance,
            )
            current = int(state.progress_current or 0)
            target = int(state.progress_target or 0)
            objective_lines.append(f"- {label} ({current}/{target})")

        lines = [quest_instance.template.name]
        recap = str(
            format_actor_msg(
                str(step.get("recap") or "").strip(),
                player,
                character=player,
                quest_instance=quest_instance,
            )
            or ""
        ).strip()
        if recap:
            lines.append(recap)
        if objective_lines:
            lines.append("Objectives:")
            lines.extend(objective_lines)

        if _preview_delivery_completion(
            quest_instance,
            step,
            player=player,
            mob_template_id=mob_template_id,
            inventory_counts=inventory_counts,
        ):
            if selector:
                lines.append(f"You have what they need. Turn it in with: give <item> {selector}")
            else:
                lines.append("You have what they need. Turn it in with: give <item> <mob>")
        elif selector:
            lines.append(f"Turn items in with: give <item> {selector}")
        else:
            lines.append("Turn items in with: give <item> <mob>")
        return "\n".join(lines)

    return None


def room_mob_quest_indicator_map(player, room_mobs) -> dict[int, dict[str, bool]]:
    room_mobs = [mob for mob in room_mobs if getattr(mob, "template_id", None)]
    if not room_mobs:
        return {}

    opportunities_by_template_id = available_npc_dialogue_opportunities_for_room_mobs(player, room_mobs)
    inventory_counts = _inventory_template_counts(player)
    active_instances = list(active_instances_qs(player))
    indicators: dict[int, dict[str, bool]] = {}

    for mob in room_mobs:
        template_id = int(mob.template_id)
        complete = False
        for quest_instance in active_instances:
            step = get_step(quest_instance.template, quest_instance.current_step_id)
            if not step or str(step.get("kind") or "").strip().lower() != "objective":
                continue
            if _preview_talk_completion(
                quest_instance,
                step,
                player=player,
                mob_template_id=template_id,
            ) or _preview_delivery_completion(
                quest_instance,
                step,
                player=player,
                mob_template_id=template_id,
                inventory_counts=inventory_counts,
            ):
                complete = True
                break
        indicators[mob.id] = {
            "enquire": bool(opportunities_by_template_id.get(template_id)),
            "complete": complete,
        }

    return indicators


def room_quest_callouts(player, room_id: int | None = None) -> list[dict[str, Any]]:
    return room_prompt_callouts_for_room(
        player,
        room_id or getattr(player, "room_id", None),
    )


def build_inspect_guidance_events(player, event_data: dict[str, Any] | None) -> list[GameEvent]:
    room_id = _event_target_room_id(event_data) or getattr(player, "room_id", None)
    opportunities = available_room_prompt_opportunities_for_room(player, room_id)
    return _presented_opportunity_events(player, opportunities)


def build_talk_guidance_events(player, event_data: dict[str, Any] | None) -> list[GameEvent]:
    target_payload = event_data.get("target") if isinstance(event_data, dict) else None
    mob_template_id = _event_target_template_id(event_data)
    if not mob_template_id:
        return []

    opportunities = available_npc_dialogue_opportunities_for_mob_template(player, mob_template_id)
    if opportunities:
        return _presented_opportunity_events(player, opportunities)

    hint_text = _active_item_turn_in_hint(
        player,
        mob_template_id=mob_template_id,
        target_payload=target_payload,
    )
    if not hint_text:
        return []

    return [
        GameEvent(
            type="quest.interaction.hint",
            recipients=[player.key],
            data={"target": target_payload or {}, "hint": hint_text},
            text=hint_text,
        )
    ]
