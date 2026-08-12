from __future__ import annotations

from typing import Any

from builders.models import AbilityDefinition
from core.abilities import definition_world
from core.combat_formulas import get_world_combat_system
from core.stat_system import get_world_stat_system


def _format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _rounds_phrase(rounds: int, label: str) -> str:
    unit = "round" if label or rounds == 1 else "rounds"
    return f"{rounds} {unit} {label}"


def _target_phrase(ability: AbilityDefinition, component: dict[str, Any] | None = None) -> str:
    target = str((component or {}).get("target") or "").strip().lower()
    target_type = str((ability.target or {}).get("type") or "hostile").strip().lower()
    if target in {"actor", "self"} or target_type == "self":
        return "yourself"
    return "the target"


def _resource_label(ability: AbilityDefinition, resource: str) -> str:
    world = definition_world(ability.world)
    labels = (get_world_stat_system(world).get("labels") or {}).get("resources") or {}
    label = str(labels.get(resource) or resource).strip() or resource
    return label.lower()


def _damage_type_label(ability: AbilityDefinition, component: dict[str, Any]) -> str:
    world = definition_world(ability.world)
    combat_system = get_world_combat_system(world)
    profile_key = str(component.get("profile") or "").strip().lower()
    profile = dict((combat_system.get("profiles") or {}).get(profile_key) or {})
    profile.update(component.get("overrides") or {})
    damage_type = str(profile.get("damage_type") or profile_key or "damage").strip().lower()
    if damage_type == "healing":
        return "healing"
    return f"{damage_type} damage"


def _component_multiplier(ability: AbilityDefinition, component: dict[str, Any]) -> float:
    world = definition_world(ability.world)
    combat_system = get_world_combat_system(world)
    profile_key = str(component.get("profile") or "").strip().lower()
    profile = dict((combat_system.get("profiles") or {}).get(profile_key) or {})
    profile.update(component.get("overrides") or {})
    try:
        return float(profile.get("multiplier", 1) or 1)
    except (TypeError, ValueError):
        return 1.0


def _output_component_phrase(ability: AbilityDefinition, component: dict[str, Any]) -> str:
    output = _damage_type_label(ability, component)
    multiplier = _component_multiplier(ability, component)
    multiplier_text = "" if multiplier == 1 else f"{_format_number(multiplier)}x "
    target = _target_phrase(ability, component)
    if component.get("type") == "healing":
        return f"heals {target} for {multiplier_text}{output}".strip()
    return f"inflicts {multiplier_text}{output} on {target}".strip()


def _effect_prevents_action(component: dict[str, Any], action: str) -> bool:
    for primitive in component.get("primitives") or []:
        if not isinstance(primitive, dict):
            continue
        if str(primitive.get("type") or "").strip().lower() != "action_rule":
            continue
        if str(primitive.get("phase") or "").strip().lower() != "before_action":
            continue
        if str(primitive.get("rule") or "").strip().lower() != "prevent":
            continue
        actions = primitive.get("actions") or []
        if isinstance(actions, str):
            actions = [actions]
        if not isinstance(actions, list):
            continue
        if action in {str(value).strip().lower() for value in actions}:
            return True
    return False


def _effect_component_phrase(ability: AbilityDefinition, component: dict[str, Any]) -> str:
    effect = str(component.get("effect") or "").strip().lower()
    target = _target_phrase(ability, component)
    duration = component.get("duration") or {}
    try:
        rounds = int(duration.get("rounds") or 0)
    except (TypeError, ValueError):
        rounds = 0
    duration_text = f" for {_rounds_phrase(rounds, '')}".rstrip() if rounds > 0 else ""
    landed_text = " if it lands" if component.get("apply") == "on_hit" else ""
    if _effect_prevents_action(component, "flee"):
        target = "you" if target == "yourself" else target
        return f"prevents {target} from fleeing{duration_text}{landed_text}"
    if effect == "stun":
        return f"stuns {target}{duration_text}{landed_text}"
    if effect == "dot":
        return f"applies damage over time to {target}{duration_text}{landed_text}"
    if effect == "hot":
        return f"applies healing over time to {target}{duration_text}{landed_text}"
    return f"applies {effect or 'an effect'} to {target}{duration_text}{landed_text}"


def _state_component_phrase(component: dict[str, Any]) -> str:
    scope = str(component.get("scope") or "character").strip().lower()
    return f"updates {scope} state"


def _interrupt_component_phrase(component: dict[str, Any]) -> str:
    landed_text = " if it lands" if component.get("apply") == "on_hit" else ""
    return f"interrupts the target's active cast or channel{landed_text}"


def _cost_sentence(ability: AbilityDefinition) -> str:
    cost = ability.cost or {}
    resource = str(cost.get("resource") or "").strip().lower()
    if not resource:
        return ""
    try:
        amount = float(cost.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return ""

    label = _resource_label(ability, resource)
    calc = str(cost.get("calc") or "fixed").strip().lower()
    if calc == "percent_base":
        return f"Costs {_format_number(amount)}% of base {label}."
    if calc == "percent_max":
        return f"Costs {_format_number(amount)}% of maximum {label}."
    return f"Costs {_format_number(amount)} {label}."


def _authored_help_text(ability: AbilityDefinition) -> str:
    help_data = ability.help or {}
    if isinstance(help_data, str):
        return help_data.strip()
    if isinstance(help_data, dict):
        return str(help_data.get("text") or "").strip()
    return ""


def generated_ability_help_text(ability: AbilityDefinition) -> str:
    phrases: list[str] = []
    cast_rounds = int((ability.cast_time or {}).get("rounds") or 0)
    if cast_rounds > 0:
        phrases.append(_rounds_phrase(cast_rounds, "cast"))

    cooldown_rounds = int((ability.cooldown or {}).get("rounds") or 0)
    if cooldown_rounds > 0:
        phrases.append(_rounds_phrase(cooldown_rounds, "cooldown"))

    for component in ability.components or []:
        component_type = component.get("type")
        if component_type in {"damage", "healing"}:
            phrases.append(_output_component_phrase(ability, component))
        elif component_type == "effect":
            phrases.append(_effect_component_phrase(ability, component))
        elif component_type == "state":
            phrases.append(_state_component_phrase(component))
        elif component_type == "interrupt":
            phrases.append(_interrupt_component_phrase(component))

    if not phrases:
        phrases.append("uses the ability")

    main = ", ".join(phrases).strip()
    if main and main[0].isalpha():
        main = main[0].upper() + main[1:]
    if not main.endswith("."):
        main += "."

    cost = _cost_sentence(ability)
    if cost:
        return f"{main} {cost}"
    return main


def ability_help_text(ability: AbilityDefinition) -> tuple[str, str]:
    authored = _authored_help_text(ability)
    if authored:
        return _with_ability_name(ability, authored), "authored"
    return _with_ability_name(ability, generated_ability_help_text(ability)), "generated"


def _with_ability_name(ability: AbilityDefinition, text: str) -> str:
    name = str(ability.name or ability.slug).strip() or ability.slug
    prefix = f"{name} - "
    if text.lower().startswith(prefix.lower()):
        return text
    return f"{prefix}{text}"
