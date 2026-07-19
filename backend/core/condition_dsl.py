from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.scoped_state import resolve_state_path


COMPARISON_OPERATORS = ("eq", "ne", "gte", "lte")
STRUCTURED_CONDITION_OPERATORS = (
    "always",
    "all",
    "any",
    "not",
    "mob_present",
    "item_present",
    "quest_completed",
    "objective_complete",
    *COMPARISON_OPERATORS,
    "in",
)


@dataclass(frozen=True)
class ConditionContext:
    actor: Any = None
    player: Any = None
    room: Any = None
    zone: Any = None
    world: Any = None
    template: Any = None
    quest_instance: Any = None
    event_data: dict[str, Any] | None = None
    objective_state_map: dict[str, Any] | None = None
    ability: Any = None
    actor_data: dict[str, Any] | None = None
    room_data: dict[str, Any] | None = None
    world_data: dict[str, Any] | None = None


def structured_condition_payload(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if not (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def is_structured_condition_mapping(value: Any) -> bool:
    return isinstance(value, dict) and any(
        operator in value for operator in STRUCTURED_CONDITION_OPERATORS
    )


def validate_condition_payload(condition: Any, *, field_name: str = "condition") -> None:
    if condition in (None, {}, []):
        return
    if isinstance(condition, bool):
        return
    if isinstance(condition, list):
        for index, item in enumerate(condition):
            validate_condition_payload(item, field_name=f"{field_name}[{index}]")
        return
    if not isinstance(condition, dict):
        return

    recognized = [
        operator for operator in STRUCTURED_CONDITION_OPERATORS
        if operator in condition
    ]
    if not recognized:
        raise ValueError(f"{field_name} must use a supported condition operator.")

    if "all" in condition:
        children = condition.get("all")
        if not isinstance(children, list):
            raise ValueError(f"{field_name}.all must be a list.")
        for index, item in enumerate(children):
            validate_condition_payload(item, field_name=f"{field_name}.all[{index}]")

    if "any" in condition:
        children = condition.get("any")
        if not isinstance(children, list):
            raise ValueError(f"{field_name}.any must be a list.")
        for index, item in enumerate(children):
            validate_condition_payload(item, field_name=f"{field_name}.any[{index}]")

    if "not" in condition:
        validate_condition_payload(condition.get("not"), field_name=f"{field_name}.not")

    if "mob_present" in condition:
        spec = condition.get("mob_present")
        ref = spec.get("ref") if isinstance(spec, dict) else spec
        if isinstance(spec, dict):
            if not str(ref or "").strip():
                raise ValueError(f"{field_name}.mob_present.ref is required.")
            unsupported_keys = sorted(set(spec) - {"ref", "count"})
            if unsupported_keys:
                raise ValueError(
                    f"{field_name}.mob_present has unsupported field(s): "
                    f"{', '.join(unsupported_keys)}."
                )
            count = spec.get("count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError(
                    f"{field_name}.mob_present.count must be a positive integer."
                )
        elif (
            isinstance(spec, bool)
            or not isinstance(spec, (str, int))
            or not str(spec).strip()
        ):
            raise ValueError(
                f"{field_name}.mob_present must be a mob definition ref or a mapping."
            )
        if isinstance(ref, str):
            prefix, separator, _ = ref.strip().partition(".")
            if separator:
                from quests.entity_refs import canonical_entity_type

                if canonical_entity_type(prefix) != "mobdefinition":
                    raise ValueError(
                        f"{field_name}.mob_present must reference a mobdefinition."
                    )

    if "item_present" in condition:
        spec = condition.get("item_present")
        if not isinstance(spec, dict):
            raise ValueError(f"{field_name}.item_present must be a mapping.")
        unsupported_keys = sorted(set(spec) - {"location", "item", "count"})
        if unsupported_keys:
            raise ValueError(
                f"{field_name}.item_present has unsupported field(s): "
                f"{', '.join(unsupported_keys)}."
            )
        location = str(spec.get("location") or "").strip().lower()
        if location not in {"actor_inventory", "room"}:
            raise ValueError(
                f"{field_name}.item_present.location must be actor_inventory or room."
            )
        item_ref = spec.get("item")
        if (
            isinstance(item_ref, bool)
            or not isinstance(item_ref, (str, int))
            or not str(item_ref).strip()
        ):
            raise ValueError(f"{field_name}.item_present.item is required.")
        if isinstance(item_ref, str):
            prefix, separator, _ = item_ref.strip().partition(".")
            if separator:
                from quests.entity_refs import canonical_entity_type

                if canonical_entity_type(prefix) != "itemdefinition":
                    raise ValueError(
                        f"{field_name}.item_present.item must reference an "
                        "itemdefinition."
                    )
        count = spec.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(
                f"{field_name}.item_present.count must be a positive integer."
            )

    for operator in COMPARISON_OPERATORS + ("in",):
        if operator not in condition:
            continue
        raw_args = condition.get(operator)
        if not isinstance(raw_args, (list, tuple)) or len(raw_args) != 2:
            raise ValueError(f"{field_name}.{operator} must be a two-item list.")


def _walk_value(value: Any, segments: list[str]) -> Any:
    current = value
    for segment in segments:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(segment)
            continue
        if isinstance(current, list):
            if not str(segment).isdigit():
                return None
            idx = int(segment)
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
            continue
        current = getattr(current, segment, None)
    return current


def _walk_context_value(primary: Any, fallback: Any, segments: list[str]) -> Any:
    if primary is not None:
        value = _walk_value(primary, segments)
        if value is not None:
            return value
    return _walk_value(fallback, segments)


def _context_actor(context: ConditionContext) -> Any:
    return context.actor if context.actor is not None else context.player


def _context_player(context: ConditionContext) -> Any:
    if context.player is not None:
        return context.player
    actor = context.actor
    if getattr(getattr(actor, "__class__", None), "__name__", "") == "Player":
        return actor
    return None


def _context_room(context: ConditionContext) -> Any:
    actor = _context_actor(context)
    return context.room or getattr(actor, "room", None)


def _context_zone(context: ConditionContext) -> Any:
    room = _context_room(context)
    actor = _context_actor(context)
    return (
        context.zone
        or getattr(room, "zone", None)
        or getattr(getattr(actor, "room", None), "zone", None)
    )


def _context_world(context: ConditionContext) -> Any:
    actor = _context_actor(context)
    room = _context_room(context)
    zone = _context_zone(context)
    return (
        context.world
        or getattr(context.template, "world", None)
        or getattr(context.ability, "world", None)
        or getattr(room, "world", None)
        or getattr(zone, "world", None)
        or getattr(actor, "world", None)
    )


def _condition_ref_world(context: ConditionContext) -> Any:
    world = _context_world(context)
    return getattr(world, "context", None) or world


def _actor_currency_balance(
    context: ConditionContext,
    actor: Any,
    code: str,
) -> int:
    try:
        from spawns.models import Player, PlayerCurrencyBalance
    except Exception:
        return 0
    if not isinstance(actor, Player):
        return 0

    actor_data = context.actor_data or {}
    data_balances = (actor_data.get("economy") or {}).get("balances")
    if isinstance(data_balances, dict):
        return int(data_balances.get(code, 0) or 0)
    if actor is None or getattr(actor, "pk", None) is None:
        return 0
    snapshot = getattr(actor, "_currency_condition_snapshot", None)
    if snapshot is None:
        try:
            snapshot = {
                currency_code: int(amount)
                for currency_code, amount in PlayerCurrencyBalance.objects.filter(
                    player_id=actor.pk,
                ).values_list("currency__code", "amount")
            }
        except Exception:
            snapshot = {}
        actor._currency_condition_snapshot = snapshot
    return int(snapshot.get(code, 0) or 0)


def resolve_path(path: Any, context: ConditionContext) -> Any:
    normalized = str(path or "").strip()
    if not normalized:
        return None

    actor = _context_actor(context)
    player = _context_player(context)
    room = _context_room(context)
    zone = _context_zone(context)
    world = _context_world(context)

    if normalized.startswith("state."):
        return resolve_state_path(
            normalized,
            actor=actor,
            character=player,
            world=world,
            zone=zone,
            room=room,
            quest_instance=context.quest_instance,
        )
    if normalized.startswith("player.") or normalized.startswith("actor."):
        root, _, _remainder = normalized.partition(".")
        segments = normalized.split(".")[1:]
        if len(segments) == 2 and segments[0] == "balances":
            balance_owner = player if root == "player" else actor
            return _actor_currency_balance(
                context,
                balance_owner,
                segments[1],
            )
        return _walk_context_value(context.actor_data, actor, segments)
    if normalized.startswith("room."):
        segments = normalized.split(".")[1:]
        return _walk_context_value(context.room_data, room, segments)
    if normalized.startswith("zone."):
        return _walk_value(zone, normalized.split(".")[1:])
    if normalized.startswith("world."):
        segments = normalized.split(".")[1:]
        return _walk_context_value(context.world_data, world, segments)
    if normalized.startswith("template."):
        return _walk_value(context.template, normalized.split(".")[1:])
    if normalized.startswith("ability."):
        return _walk_value(context.ability, normalized.split(".")[1:])
    if normalized.startswith("event."):
        return _walk_value(context.event_data or {}, normalized.split(".")[1:])
    if normalized.startswith("quest.local_state."):
        state = getattr(context.quest_instance, "local_state", {}) or {}
        return _walk_value(state, normalized.split(".")[2:])
    if normalized.startswith("quest.state."):
        state = getattr(context.quest_instance, "local_state", {}) or {}
        return _walk_value(state, normalized.split(".")[2:])
    if normalized.startswith("quest.slot_bindings."):
        state = getattr(context.quest_instance, "slot_bindings", {}) or {}
        return _walk_value(state, normalized.split(".")[2:])
    if normalized == "quest.current_step_id":
        return getattr(context.quest_instance, "current_step_id", None)

    return _walk_value(context.event_data or {}, normalized.split("."))


def resolve_value(value: Any, context: ConditionContext) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}") and len(text) >= 3:
            return resolve_path(text[1:-1], context)
    return value


def _definition_ref_type_for_path(path: str, value: Any = None) -> str | None:
    if isinstance(value, str):
        prefix, sep, _ = value.strip().partition(".")
        if sep == ".":
            try:
                from quests.entity_refs import canonical_entity_type
            except Exception:
                canonical_entity_type = None
            explicit_type = canonical_entity_type(prefix) if canonical_entity_type else None
            if explicit_type:
                return explicit_type

    normalized = str(path or "").strip()
    if not normalized.endswith(".definition_id"):
        return None
    if ".item.definition_id" in normalized or ".inventory." in normalized:
        return "itemdefinition"
    return "mobdefinition"


def _path_uses_room_ref(
    path: str,
    value: Any = None,
    *,
    event_data: dict[str, Any] | None = None,
) -> bool:
    normalized = str(path or "").strip()
    if normalized in {"player.room_id", "player.room.id", "actor.room_id", "actor.room.id"}:
        return True
    if (
        normalized == "event.target.id"
        and str((event_data or {}).get("target_type") or "").strip().lower() == "room"
    ):
        return True

    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if not (
        text.startswith("room@")
        or (text.startswith("room.") and text.partition(".")[2].isdigit())
    ):
        return False
    return normalized.endswith(".id") or normalized.endswith("_id") or normalized == "event.target.id"


def _resolve_comparison_value(path: str, value: Any, context: ConditionContext) -> Any:
    world = _condition_ref_world(context)
    expected_type = _definition_ref_type_for_path(path, value)
    if expected_type and world:
        try:
            from quests.entity_refs import resolve_entity_ref_id
        except Exception:
            resolve_entity_ref_id = None
        if resolve_entity_ref_id:
            resolved_id = resolve_entity_ref_id(
                world=world,
                value=value,
                expected_type=expected_type,
            )
            if resolved_id is not None:
                return resolved_id
    if world and _path_uses_room_ref(path, value, event_data=context.event_data):
        try:
            from quests.entity_refs import resolve_room_ref_id
        except Exception:
            resolve_room_ref_id = None
        if resolve_room_ref_id:
            resolved_room_id = resolve_room_ref_id(world=world, value=value)
            if resolved_room_id is not None:
                return resolved_room_id
    return value


def _player_completed_quest_template(value: Any, context: ConditionContext) -> bool:
    player = _context_player(context)
    if not player:
        return False

    try:
        from quests.entity_refs import resolve_entity_ref_id
        from quests.models import QuestInstance
    except Exception:
        return False

    template_id = resolve_entity_ref_id(
        world=_condition_ref_world(context),
        value=resolve_value(value, context),
        expected_type="questtemplate",
    )
    if not template_id:
        return False

    return QuestInstance.objects.filter(
        player=player,
        template_id=template_id,
        status="resolved",
        resolution="complete",
    ).exists()


def _mob_definition_filter(value: Any) -> dict[str, Any] | None:
    """Return a Mob queryset filter for a supported definition reference."""
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return {"definition_id": value}

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return {"definition_id": int(text)}

    prefix, separator, raw_value = text.partition(".")
    if separator:
        try:
            from quests.entity_refs import canonical_entity_type
        except Exception:
            return None
        if canonical_entity_type(prefix) != "mobdefinition":
            return None
        text = raw_value.strip()
        if not text:
            return None
        if text.isdigit():
            return {"definition_id": int(text)}

    return {"definition__slug": text}


def _mob_present(value: Any, context: ConditionContext) -> bool:
    spec = value if isinstance(value, dict) else {"ref": value}
    definition_filter = _mob_definition_filter(
        resolve_value(spec.get("ref"), context)
    )
    count = spec.get("count", 1)
    if (
        not definition_filter
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
    ):
        return False

    actor = _context_actor(context)
    room = _context_room(context)
    runtime_world_id = getattr(actor, "world_id", None) or getattr(
        _context_world(context),
        "pk",
        None,
    )
    if not room or not runtime_world_id:
        return False

    try:
        from django.db.models import F, Q
        from spawns.models import Mob
    except Exception:
        return False

    # Runtime worlds point at their authored world through ``context``. Instance
    # runtime worlds point at an instance template whose ``instance_of`` is the
    # authored world. Keep that resolution in the EXISTS query so evaluating a
    # room-presence condition does not load an entire World row first.
    definition_world_scope = (
        Q(
            world__context_id__isnull=True,
            definition__world_id=F("world_id"),
        )
        | Q(
            world__context_id__isnull=False,
            world__context__instance_of_id__isnull=True,
            definition__world_id=F("world__context_id"),
        )
        | Q(
            world__context__instance_of_id__isnull=False,
            definition__world_id=F("world__context__instance_of_id"),
        )
    )
    mobs = Mob.objects.filter(
        world_id=runtime_world_id,
        room_id=getattr(room, "pk", None),
        is_pending_deletion=False,
        **definition_filter,
    ).filter(definition_world_scope)
    if count == 1:
        return mobs.exists()
    return mobs.count() >= count


def _item_definition_filter(value: Any) -> dict[str, Any] | None:
    """Return an Item queryset filter for a supported definition reference."""
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return {"definition_id": value}

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return {"definition_id": int(text)}

    prefix, separator, raw_value = text.partition(".")
    if separator:
        try:
            from quests.entity_refs import canonical_entity_type
        except Exception:
            return None
        if canonical_entity_type(prefix) != "itemdefinition":
            return None
        text = raw_value.strip()
        if not text:
            return None
        # A typed item reference is always a portable slug reference, even
        # when the slug contains digits only. Bare numeric values retain the
        # legacy database-id meaning.
        return {"definition__slug": text}

    return {"definition__slug": text}


def _item_present(value: Any, context: ConditionContext) -> bool:
    if not isinstance(value, dict):
        return False

    location = str(value.get("location") or "").strip().lower()
    definition_filter = _item_definition_filter(
        resolve_value(value.get("item"), context)
    )
    count = value.get("count", 1)
    if (
        location not in {"actor_inventory", "room"}
        or not definition_filter
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
    ):
        return False

    actor = _context_actor(context)
    runtime_world_id = getattr(actor, "world_id", None) or getattr(
        _context_world(context),
        "pk",
        None,
    )
    container = actor if location == "actor_inventory" else _context_room(context)
    if container is None or runtime_world_id is None or not hasattr(container, "inventory"):
        return False

    try:
        from django.db.models import F, Q
    except Exception:
        return False

    definition_world_scope = (
        Q(
            world__context_id__isnull=True,
            definition__world_id=F("world_id"),
        )
        | Q(
            world__context_id__isnull=False,
            world__context__instance_of_id__isnull=True,
            definition__world_id=F("world__context_id"),
        )
        | Q(
            world__context__instance_of_id__isnull=False,
            definition__world_id=F("world__context__instance_of_id"),
        )
    )
    items = container.inventory.filter(
        world_id=runtime_world_id,
        is_pending_deletion=False,
        **definition_filter,
    ).filter(definition_world_scope)
    if count == 1:
        return items.exists()
    return items.count() >= count


def evaluate_condition(
    condition: Any,
    *,
    context: ConditionContext | None = None,
    **context_kwargs: Any,
) -> bool:
    if context is None:
        context = ConditionContext(**context_kwargs)

    if condition in (None, {}, []):
        return True
    if isinstance(condition, bool):
        return condition
    if isinstance(condition, list):
        return all(evaluate_condition(item, context=context) for item in condition)
    if not isinstance(condition, dict):
        return bool(condition)

    if "always" in condition:
        return bool(condition.get("always"))
    if "all" in condition:
        return all(
            evaluate_condition(item, context=context)
            for item in condition.get("all") or []
        )
    if "any" in condition:
        return any(
            evaluate_condition(item, context=context)
            for item in condition.get("any") or []
        )
    if "not" in condition:
        return not evaluate_condition(condition.get("not"), context=context)
    if "mob_present" in condition:
        return _mob_present(condition.get("mob_present"), context)
    if "item_present" in condition:
        return _item_present(condition.get("item_present"), context)
    if "objective_complete" in condition:
        objective_id = str(condition.get("objective_complete") or "").strip()
        if not objective_id:
            return False
        state = (context.objective_state_map or {}).get(objective_id)
        return bool(state and getattr(state, "status", None) == "complete")
    if "quest_completed" in condition:
        return _player_completed_quest_template(condition.get("quest_completed"), context)

    comparisons = (
        ("eq", lambda left, right: left == right),
        ("ne", lambda left, right: left != right),
        ("gte", lambda left, right: left is not None and right is not None and left >= right),
        ("lte", lambda left, right: left is not None and right is not None and left <= right),
    )
    for operator, predicate in comparisons:
        if operator not in condition:
            continue
        raw_args = condition.get(operator) or []
        if not isinstance(raw_args, (list, tuple)) or len(raw_args) != 2:
            return False
        left_path = str(raw_args[0])
        left = resolve_path(left_path, context)
        right = _resolve_comparison_value(
            left_path,
            resolve_value(raw_args[1], context),
            context,
        )
        return predicate(left, right)

    if "in" in condition:
        raw_args = condition.get("in") or []
        if not isinstance(raw_args, (list, tuple)) or len(raw_args) != 2:
            return False
        left_path = str(raw_args[0])
        left = resolve_path(left_path, context)
        candidates = resolve_value(raw_args[1], context)
        if not isinstance(candidates, (list, tuple, set)):
            return False
        resolved_candidates = [
            _resolve_comparison_value(left_path, candidate, context)
            for candidate in candidates
        ]
        return left in resolved_candidates

    return False
