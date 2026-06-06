from __future__ import annotations

from typing import Any

from core.condition_dsl import (
    ConditionContext,
    evaluate_condition as evaluate_structured_condition,
    resolve_path as resolve_condition_path,
    resolve_value as resolve_condition_value,
)


def _context(
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
    objective_state_map: dict[str, Any] | None = None,
) -> ConditionContext:
    return ConditionContext(
        actor=player,
        player=player,
        room=getattr(player, "room", None),
        zone=getattr(getattr(player, "room", None), "zone", None),
        world=getattr(player, "world", None) or getattr(template, "world", None),
        template=template,
        quest_instance=quest_instance,
        event_data=event_data,
        objective_state_map=objective_state_map,
    )


def resolve_path(
    path: str,
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
) -> Any:
    return resolve_condition_path(
        path,
        _context(
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=event_data,
        ),
    )


def resolve_value(
    value: Any,
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
) -> Any:
    return resolve_condition_value(
        value,
        _context(
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=event_data,
        ),
    )


def evaluate_condition(
    condition: Any,
    *,
    player=None,
    template=None,
    quest_instance=None,
    event_data: dict[str, Any] | None = None,
    objective_state_map: dict[str, Any] | None = None,
) -> bool:
    return evaluate_structured_condition(
        condition,
        context=_context(
            player=player,
            template=template,
            quest_instance=quest_instance,
            event_data=event_data,
            objective_state_map=objective_state_map,
        ),
    )
