from __future__ import annotations

from typing import Any

from quests.entity_refs import canonical_template_type, resolve_template_ref_id


def _walk_value(value: Any, segments: list[str]) -> Any:
    current = value
    for segment in segments:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(segment)
            continue
        if isinstance(current, list):
            if not segment.isdigit():
                return None
            idx = int(segment)
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
            continue
        current = getattr(current, segment, None)
    return current


def resolve_path(
    path: str,
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
) -> Any:
    if not path:
        return None

    path = str(path).strip()
    if not path:
        return None

    if path.startswith("player."):
        return _walk_value(player, path.split(".")[1:])
    if path.startswith("template."):
        return _walk_value(template, path.split(".")[1:])
    if path.startswith("event."):
        return _walk_value(event_data or {}, path.split(".")[1:])
    if path.startswith("quest.local_state."):
        state = getattr(quest_instance, "local_state", {}) or {}
        return _walk_value(state, path.split(".")[2:])
    if path.startswith("quest.slot_bindings."):
        state = getattr(quest_instance, "slot_bindings", {}) or {}
        return _walk_value(state, path.split(".")[2:])
    if path == "quest.current_step_id":
        return getattr(quest_instance, "current_step_id", None)
    return _walk_value(event_data or {}, path.split("."))


def resolve_value(
    value: Any,
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}") and len(text) >= 3:
            return resolve_path(
                text[1:-1],
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
            )
    return value


def _template_ref_type_for_path(path: str, value: Any = None) -> str | None:
    if isinstance(value, str):
        prefix, sep, _ = value.strip().partition(".")
        if sep == ".":
            explicit_type = canonical_template_type(prefix)
            if explicit_type:
                return explicit_type

    path = str(path or "").strip()
    if not path.endswith(".template_id"):
        return None
    if ".item.template_id" in path:
        return "itemtemplate"
    return "mobtemplate"


def _resolve_comparison_value(
    path: str,
    value: Any,
    *,
    player=None,
    template=None,
) -> Any:
    expected_type = _template_ref_type_for_path(path, value)
    world = getattr(template, "world", None) or getattr(player, "world", None)
    if expected_type and world:
        resolved_id = resolve_template_ref_id(
            world=world,
            value=value,
            expected_type=expected_type,
        )
        if resolved_id is not None:
            return resolved_id
    return value


def evaluate_condition(
    condition: Any,
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
    objective_state_map: dict[str, Any] | None = None,
) -> bool:
    if condition in (None, {}, []):
        return True

    if isinstance(condition, bool):
        return condition

    if isinstance(condition, list):
        return all(
            evaluate_condition(
                item,
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
                objective_state_map=objective_state_map,
            )
            for item in condition
        )

    if not isinstance(condition, dict):
        return bool(condition)

    if "always" in condition:
        return bool(condition.get("always"))

    if "all" in condition:
        return all(
            evaluate_condition(
                item,
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
                objective_state_map=objective_state_map,
            )
            for item in condition.get("all") or []
        )

    if "any" in condition:
        return any(
            evaluate_condition(
                item,
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
                objective_state_map=objective_state_map,
            )
            for item in condition.get("any") or []
        )

    if "not" in condition:
        return not evaluate_condition(
            condition.get("not"),
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=event_data,
            objective_state_map=objective_state_map,
        )

    if "objective_complete" in condition:
        objective_id = str(condition.get("objective_complete") or "").strip()
        if not objective_id:
            return False
        state = (objective_state_map or {}).get(objective_id)
        return bool(state and state.status == "complete")

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
        left = resolve_path(
            str(raw_args[0]),
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=event_data,
        )
        right = _resolve_comparison_value(
            str(raw_args[0]),
            resolve_value(
                raw_args[1],
                player=player,
                template=template,
                quest_instance=quest_instance,
                event_data=event_data,
            ),
            player=player,
            template=template,
        )
        return predicate(left, right)

    if "in" in condition:
        raw_args = condition.get("in") or []
        if not isinstance(raw_args, (list, tuple)) or len(raw_args) != 2:
            return False
        left = resolve_path(
            str(raw_args[0]),
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=event_data,
        )
        candidates = resolve_value(
            raw_args[1],
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=event_data,
        )
        if not isinstance(candidates, (list, tuple, set)):
            return False
        resolved_candidates = [
            _resolve_comparison_value(
                str(raw_args[0]),
                candidate,
                player=player,
                template=template,
            )
            for candidate in candidates
        ]
        return left in resolved_candidates

    return False
