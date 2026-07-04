from __future__ import annotations

from copy import deepcopy
from typing import Any

from django.db.models import Q

from spawns.models import Player


ROOM_ALLY_TARGETS = {"room.allies", "room.players"}
CHARACTER_SELF_TARGETS = {"actor", "self", "effect.source"}


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value or default))
    except (TypeError, ValueError):
        return default


def actor_effect_ref(actor: Any) -> dict[str, Any]:
    actor_type = "player" if isinstance(actor, Player) else "mob"
    return {"type": actor_type, "id": int(getattr(actor, "id", 0) or 0)}


def active_character_effects(actor: Any) -> list[dict[str, Any]]:
    effects = getattr(actor, "active_effects", []) or []
    if not isinstance(effects, list):
        return []
    return [
        deepcopy(effect)
        for effect in effects
        if isinstance(effect, dict) and _positive_int(effect.get("remaining_rounds")) > 0
    ]


def _combat_modifier_primitives(component: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        primitive
        for primitive in component.get("primitives") or []
        if isinstance(primitive, dict) and primitive.get("type") == "combat_modifier"
    ]


def _stat_modifier_primitives(component: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        primitive
        for primitive in component.get("primitives") or []
        if isinstance(primitive, dict) and primitive.get("type") == "stat_modifier"
    ]


def component_targets_character_effect(
    component: dict[str, Any],
    *,
    ability: Any | None = None,
) -> bool:
    target_selector = str(component.get("target") or "").strip().lower()
    if target_selector in ROOM_ALLY_TARGETS:
        return True
    if _combat_modifier_primitives(component):
        return True
    if not _stat_modifier_primitives(component):
        return False
    if target_selector in CHARACTER_SELF_TARGETS:
        return True
    if target_selector == "ability.target":
        ability_target = str(
            (getattr(ability, "target", None) or {}).get("type") or ""
        ).strip().lower()
        return ability_target == "self"
    return False


def room_ally_players(actor: Player, *, room=None) -> list[Player]:
    current_room = room or getattr(actor, "room", None)
    if current_room is None:
        return [actor]
    return list(
        Player.objects.select_for_update()
        .filter(world=actor.world, room=current_room)
        .filter(Q(in_game=True) | Q(pk=actor.pk))
        .order_by("id")
    )


def targets_for_character_effect_component(
    *,
    actor: Player,
    component: dict[str, Any],
    ability: Any | None = None,
    room=None,
) -> list[Player]:
    target_selector = str(component.get("target") or "self").strip().lower()
    if target_selector in ROOM_ALLY_TARGETS:
        targets: list[Player] = []
        seen: set[int] = set()
        for target in room_ally_players(actor, room=room):
            if target.id in seen:
                continue
            seen.add(target.id)
            targets.append(target)
        return targets
    if target_selector == "ability.target":
        ability_target = str(
            (getattr(ability, "target", None) or {}).get("type") or ""
        ).strip().lower()
        if ability_target == "self":
            return [actor]
    return [actor]


def build_character_effect(
    *,
    component: dict[str, Any],
    source: Player,
    target: Player,
    round_id: str | None = None,
) -> dict[str, Any]:
    duration = _positive_int((component.get("duration") or {}).get("rounds"), default=1) or 1
    text = component.get("text") or {}
    effect_key = str(component.get("effect") or "").strip().lower()
    effect = {
        "effect": effect_key,
        "category": str(component.get("category") or "neutral").strip().lower(),
        "scope": "character",
        "source": actor_effect_ref(source),
        "target": actor_effect_ref(target),
        "remaining_rounds": duration,
        "duration_rounds": duration,
        "rounds_elapsed": 0,
        "label": str(text.get("label") or effect_key or "Effect").strip(),
        "primitives": deepcopy(component.get("primitives") or []),
    }
    stack_key = str(component.get("stack_key") or "").strip().lower()
    if stack_key:
        effect["stack_key"] = stack_key
    stacking = str(component.get("stacking") or "").strip().lower()
    if stacking:
        effect["stacking"] = stacking
    if round_id:
        effect["started_round_id"] = round_id
    return effect


def refresh_or_add_character_effect(target: Player, effect: dict[str, Any]) -> str:
    effects = active_character_effects(target)
    stack_key = str(effect.get("stack_key") or "").strip().lower()
    stacking = str(effect.get("stacking") or "independent").strip().lower()
    if not stack_key or stacking != "refresh":
        effects.append(effect)
        target.active_effects = effects
        return "applied"

    refreshed = False
    next_effects: list[dict[str, Any]] = []
    for existing in effects:
        if str(existing.get("stack_key") or "").strip().lower() != stack_key:
            next_effects.append(existing)
            continue
        if not refreshed:
            next_effects.append(effect)
            refreshed = True

    if not refreshed:
        next_effects.append(effect)

    target.active_effects = next_effects
    return "refreshed" if refreshed else "applied"


def advance_character_effect_durations(
    actor: Any,
    *,
    current_round_id: str | None = None,
) -> bool:
    if not hasattr(actor, "active_effects"):
        return False

    original = getattr(actor, "active_effects", []) or []
    effects = active_character_effects(actor)
    kept: list[dict[str, Any]] = []
    changed = not isinstance(original, list) or len(original) != len(effects)

    for effect in effects:
        if current_round_id and effect.get("started_round_id") == current_round_id:
            kept.append(effect)
            continue

        remaining = _positive_int(effect.get("remaining_rounds")) - 1
        if remaining > 0:
            updated = {
                **effect,
                "remaining_rounds": remaining,
                "rounds_elapsed": _positive_int(effect.get("rounds_elapsed")) + 1,
            }
            kept.append(updated)
            if updated != effect:
                changed = True
        else:
            changed = True

    if kept != original:
        actor.active_effects = kept
        changed = True
    return changed
