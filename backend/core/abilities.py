"""
Declarative WR2 ability schema support.

The combat resolver should not parse builder-authored YAML while a round is
resolving. Ability manifests normalize into this compact shape at import time,
then runtime code consumes the normalized JSON directly.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from core.condition_dsl import (
    is_structured_condition_mapping,
    validate_condition_payload,
)


class AbilityValidationError(ValueError):
    pass


SUPPORTED_ABILITY_VERSION = 1
DEFAULT_MAX_KNOWN_ABILITIES = 8
UNCAPPED_MAX_KNOWN_ABILITIES = "uncapped"

ACTION_TYPES = ("primary", "utility")
TARGET_TYPES = ("hostile", "self", "ally")
TARGET_DEFAULTS = ("current_target", "self")
COST_RESOURCES = ("health", "energy", "stamina")
COST_CALCS = ("fixed", "percent_max")
COMPONENT_TYPES = ("damage", "healing", "effect")
EFFECT_TYPES = ("stun", "dot", "hot")
EFFECT_APPLY_POLICIES = ("on_resolve", "on_hit")

ABILITY_DEFINITION_FIELDS = {
    "version",
    "command",
    "action_type",
    "target",
    "availability",
    "requirements",
    "cost",
    "cooldown",
    "components",
    "is_active",
}


def default_ability_progression() -> dict[str, Any]:
    return {
        "max_known": DEFAULT_MAX_KNOWN_ABILITIES,
    }


def normalize_ability_progression(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise AbilityValidationError("ability_progression must be a mapping.")

    unknown_fields = sorted(set(value.keys()) - {"max_known"})
    if unknown_fields:
        raise AbilityValidationError(
            "ability_progression has unsupported field(s): "
            + ", ".join(unknown_fields)
            + "."
        )

    raw_max_known = value.get("max_known", DEFAULT_MAX_KNOWN_ABILITIES)
    if isinstance(raw_max_known, str):
        max_known = raw_max_known.strip().lower()
        if max_known != UNCAPPED_MAX_KNOWN_ABILITIES:
            raise AbilityValidationError(
                "ability_progression.max_known must be a positive integer or uncapped."
            )
        return {"max_known": UNCAPPED_MAX_KNOWN_ABILITIES}

    if isinstance(raw_max_known, bool):
        raise AbilityValidationError(
            "ability_progression.max_known must be a positive integer or uncapped."
        )
    try:
        max_known_int = int(raw_max_known)
    except (TypeError, ValueError):
        raise AbilityValidationError(
            "ability_progression.max_known must be a positive integer or uncapped."
        )
    if max_known_int < 1:
        raise AbilityValidationError(
            "ability_progression.max_known must be >= 1 or uncapped."
        )
    return {"max_known": max_known_int}


def max_known_abilities_for_world(world: Any) -> int | None:
    config = getattr(world, "effective_config", None) or getattr(world, "config", None)
    if config is None:
        return DEFAULT_MAX_KNOWN_ABILITIES
    progression = normalize_ability_progression(
        getattr(config, "ability_progression", None)
    )
    max_known = progression["max_known"]
    if max_known == UNCAPPED_MAX_KNOWN_ABILITIES:
        return None
    return int(max_known)


def definition_world(world: Any) -> Any:
    return getattr(world, "config_source_world", None) or getattr(world, "context", None) or world


def _coerce_slug(value: Any, *, field_name: str, allow_hyphen: bool = True) -> str:
    text = str(value if value is not None else "").strip().lower()
    pattern = r"^[a-z][a-z0-9_-]*$" if allow_hyphen else r"^[a-z][a-z0-9_]*$"
    if not text or not re.match(pattern, text):
        raise AbilityValidationError(
            f"{field_name} must be a lowercase slug using letters, numbers, "
            + ("hyphens, " if allow_hyphen else "")
            + "and underscores."
        )
    return text


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise AbilityValidationError(f"{field_name} must be true or false.")
    return value


def _coerce_number(
    value: Any,
    *,
    field_name: str,
    minimum: float | None = None,
) -> float:
    if isinstance(value, bool):
        raise AbilityValidationError(f"{field_name} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AbilityValidationError(f"{field_name} must be a number.")
    if not math.isfinite(number):
        raise AbilityValidationError(f"{field_name} must be a finite number.")
    if minimum is not None and number < minimum:
        raise AbilityValidationError(f"{field_name} must be >= {minimum}.")
    return number


def _coerce_positive_int(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise AbilityValidationError(f"{field_name} must be an integer.")
    try:
        integer = int(value)
    except (TypeError, ValueError):
        raise AbilityValidationError(f"{field_name} must be an integer.")
    if integer < minimum:
        raise AbilityValidationError(f"{field_name} must be >= {minimum}.")
    return integer


def _coerce_choice(value: Any, *, choices: tuple[str, ...], field_name: str) -> str:
    text = str(value or "").strip().lower()
    if text not in choices:
        raise AbilityValidationError(f"{field_name} must be one of: {', '.join(choices)}.")
    return text


def _normalize_command(value: Any, *, slug: str) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec.command must be a mapping.")

    raw_verbs = value.get("verbs", [slug.replace("-", "_")])
    if isinstance(raw_verbs, str):
        raw_verbs = [raw_verbs]
    if not isinstance(raw_verbs, list):
        raise AbilityValidationError("spec.command.verbs must be a list.")

    verbs: list[str] = []
    for index, raw_verb in enumerate(raw_verbs):
        verb = _coerce_slug(
            raw_verb,
            field_name=f"spec.command.verbs[{index}]",
            allow_hyphen=False,
        )
        if verb not in verbs:
            verbs.append(verb)
    if not verbs:
        verbs.append(_coerce_slug(slug.replace("-", "_"), field_name="metadata.slug", allow_hyphen=False))
    return {"verbs": verbs}


def _normalize_target(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec.target must be a mapping.")
    target_type = _coerce_choice(
        value.get("type", "hostile"),
        choices=TARGET_TYPES,
        field_name="spec.target.type",
    )
    default = value.get("default")
    if default in (None, ""):
        default = "self" if target_type in {"self", "ally"} else "current_target"
    default = _coerce_choice(
        default,
        choices=TARGET_DEFAULTS,
        field_name="spec.target.default",
    )
    allow_out_of_combat = value.get("allow_out_of_combat")
    if allow_out_of_combat is None:
        allow_out_of_combat = target_type in {"self", "ally"}
    return {
        "type": target_type,
        "default": default,
        "allow_out_of_combat": _coerce_bool(
            allow_out_of_combat,
            field_name="spec.target.allow_out_of_combat",
        ),
    }


def _normalize_availability(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec.availability must be a mapping.")

    classes = value.get("classes", [])
    if isinstance(classes, str):
        classes = [classes]
    if not isinstance(classes, list):
        raise AbilityValidationError("spec.availability.classes must be a list.")
    normalized_classes = [
        _coerce_slug(item, field_name="spec.availability.classes[]", allow_hyphen=True)
        for item in classes
    ]
    min_level = _coerce_positive_int(
        value.get("min_level", 1),
        field_name="spec.availability.min_level",
        minimum=1,
    )
    return {
        "classes": list(dict.fromkeys(normalized_classes)),
        "min_level": min_level,
    }


def _normalize_requirements(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec.requirements must be a mapping.")
    normalized = deepcopy(value)
    if "conditions" in normalized:
        try:
            validate_condition_payload(
                normalized.get("conditions"),
                field_name="spec.requirements.conditions",
            )
        except ValueError as exc:
            raise AbilityValidationError(str(exc))
    elif is_structured_condition_mapping(normalized):
        try:
            validate_condition_payload(normalized, field_name="spec.requirements")
        except ValueError as exc:
            raise AbilityValidationError(str(exc))
    return normalized


def _normalize_cost(value: Any) -> dict[str, Any]:
    if value in (None, "", {}):
        return {}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec.cost must be a mapping.")
    resource = _coerce_choice(
        value.get("resource"),
        choices=COST_RESOURCES,
        field_name="spec.cost.resource",
    )
    amount = _coerce_number(
        value.get("amount", 0),
        field_name="spec.cost.amount",
        minimum=0,
    )
    calc = _coerce_choice(
        value.get("calc", "fixed"),
        choices=COST_CALCS,
        field_name="spec.cost.calc",
    )
    return {
        "resource": resource,
        "amount": amount,
        "calc": calc,
    }


def _normalize_cooldown(value: Any) -> dict[str, Any]:
    if value in (None, "", {}):
        return {"rounds": 0}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec.cooldown must be a mapping.")
    return {
        "rounds": _coerce_positive_int(
            value.get("rounds", 0),
            field_name="spec.cooldown.rounds",
            minimum=0,
        )
    }


def _normalize_text(value: Any, *, default_label: str) -> dict[str, str]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise AbilityValidationError("component.text must be a mapping.")
    label = _coerce_text(value.get("label", default_label)).strip() or default_label
    return {"label": label}


def _normalize_output_component(
    value: dict[str, Any],
    *,
    field_name: str,
    component_type: str,
    default_label: str,
) -> dict[str, Any]:
    profile = _coerce_slug(
        value.get("profile", "basic_heal" if component_type == "healing" else "basic_ability"),
        field_name=f"{field_name}.profile",
        allow_hyphen=False,
    )
    overrides = value.get("overrides", {})
    if overrides in (None, ""):
        overrides = {}
    if not isinstance(overrides, dict):
        raise AbilityValidationError(f"{field_name}.overrides must be a mapping.")
    return {
        "type": component_type,
        "profile": profile,
        "overrides": deepcopy(overrides),
        "text": _normalize_text(value.get("text"), default_label=default_label),
    }


def _duration_rounds(value: Any, *, field_name: str) -> int:
    if isinstance(value, dict):
        value = value.get("rounds")
    return _coerce_positive_int(value, field_name=field_name, minimum=1)


def _normalize_effect_component(
    value: dict[str, Any],
    *,
    field_name: str,
    default_label: str,
) -> dict[str, Any]:
    raw_effect = value.get("effect")
    effect_type = _coerce_choice(
        raw_effect,
        choices=EFFECT_TYPES,
        field_name=f"{field_name}.effect",
    )
    duration_rounds = _duration_rounds(
        value.get("duration", {"rounds": 1}),
        field_name=f"{field_name}.duration.rounds",
    )
    apply_policy = _coerce_choice(
        value.get("apply", "on_resolve"),
        choices=EFFECT_APPLY_POLICIES,
        field_name=f"{field_name}.apply",
    )

    normalized: dict[str, Any] = {
        "type": "effect",
        "effect": effect_type,
        "duration": {"rounds": duration_rounds},
        "apply": apply_policy,
        "text": _normalize_text(value.get("text"), default_label=default_label),
    }
    if effect_type in {"dot", "hot"}:
        tick = value.get("tick") or {}
        if not isinstance(tick, dict):
            raise AbilityValidationError(f"{field_name}.tick must be a mapping.")
        tick_component = tick.get("component")
        if not isinstance(tick_component, dict):
            raise AbilityValidationError(f"{field_name}.tick.component must be a mapping.")
        expected_type = "damage" if effect_type == "dot" else "healing"
        normalized["tick"] = {
            "every_rounds": _coerce_positive_int(
                tick.get("every_rounds", 1),
                field_name=f"{field_name}.tick.every_rounds",
                minimum=1,
            ),
            "component": _normalize_output_component(
                {**tick_component, "type": expected_type},
                field_name=f"{field_name}.tick.component",
                component_type=expected_type,
                default_label=default_label,
            ),
        }
    return normalized


def _normalize_component(value: Any, *, field_name: str, default_label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AbilityValidationError(f"{field_name} must be a mapping.")
    component_type = str(value.get("type") or "").strip().lower()
    if component_type in EFFECT_TYPES:
        value = {**value, "type": "effect", "effect": component_type}
        component_type = "effect"
    component_type = _coerce_choice(
        component_type,
        choices=COMPONENT_TYPES,
        field_name=f"{field_name}.type",
    )
    if component_type in {"damage", "healing"}:
        return _normalize_output_component(
            value,
            field_name=field_name,
            component_type=component_type,
            default_label=default_label,
        )
    return _normalize_effect_component(
        value,
        field_name=field_name,
        default_label=default_label,
    )


def normalize_ability_definition(
    value: Any,
    *,
    slug: str,
    name: str,
) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec must be a mapping.")
    unknown_fields = sorted(set(value.keys()) - ABILITY_DEFINITION_FIELDS)
    if unknown_fields:
        raise AbilityValidationError(
            "spec has unsupported field(s): " + ", ".join(unknown_fields) + "."
        )

    version = value.get("version", SUPPORTED_ABILITY_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise AbilityValidationError("spec.version must be an integer.")
    if version != SUPPORTED_ABILITY_VERSION:
        raise AbilityValidationError(f"spec.version must be {SUPPORTED_ABILITY_VERSION}.")

    components = value.get("components")
    if not isinstance(components, list) or not components:
        raise AbilityValidationError("spec.components must be a non-empty list.")

    label = name.strip() or slug.replace("-", " ").title()
    return {
        "version": version,
        "command": _normalize_command(value.get("command"), slug=slug),
        "action_type": _coerce_choice(
            value.get("action_type", "primary"),
            choices=ACTION_TYPES,
            field_name="spec.action_type",
        ),
        "target": _normalize_target(value.get("target")),
        "availability": _normalize_availability(value.get("availability")),
        "requirements": _normalize_requirements(value.get("requirements")),
        "cost": _normalize_cost(value.get("cost")),
        "cooldown": _normalize_cooldown(value.get("cooldown")),
        "components": [
            _normalize_component(
                component,
                field_name=f"spec.components[{index}]",
                default_label=label,
            )
            for index, component in enumerate(components)
        ],
        "is_active": _coerce_bool(
            value.get("is_active", True),
            field_name="spec.is_active",
        ),
    }
