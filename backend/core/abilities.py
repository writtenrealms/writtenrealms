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
    ConditionContext,
    evaluate_condition,
    is_structured_condition_mapping,
    validate_condition_payload,
)
from core.world_config import inherited_system_config


class AbilityValidationError(ValueError):
    pass


SUPPORTED_ABILITY_VERSION = 1
DEFAULT_MAX_KNOWN_ABILITIES = 8
UNCAPPED_MAX_KNOWN_ABILITIES = "uncapped"
ABILITY_ACTOR_TYPES = ("player", "mob")

TARGET_TYPES = ("hostile", "self", "ally")
TARGET_DEFAULTS = ("current_target", "self")
TARGET_RANGES = ("current_room", "adjacent_room", "current_or_adjacent_room")
COST_RESOURCES = ("health", "energy", "stamina")
COST_CALCS = ("fixed", "percent_max", "percent_base")
COOLDOWN_TRIGGERS = ("on_resolve", "on_hit")
COMPONENT_TYPES = ("damage", "healing", "effect", "state", "interrupt")
EFFECT_TYPES = ("stun", "dot", "hot")
EFFECT_APPLY_POLICIES = ("on_resolve", "on_hit")
INTERRUPT_TARGETS = ("ability.target",)
EFFECT_CATEGORIES = ("buff", "debuff", "neutral")
EFFECT_SCOPES = ("encounter", "character")
EFFECT_TARGETS = (
    "actor",
    "self",
    "target",
    "ability.target",
    "effect.source",
    "effect.target",
    "room.allies",
    "room.players",
)
EFFECT_TICK_PHASES = ("round_start",)
EFFECT_PRIMITIVE_TYPES = (
    "resource_change",
    "proc",
    "damage_absorb",
    "combat_modifier",
    "stat_modifier",
    "action_rule",
)
EFFECT_PROC_PHASES = ("after_damage",)
ACTION_RULE_PHASES = ("before_action",)
ACTION_RULE_RULES = ("prevent",)
ACTION_RULE_ACTIONS = ("flee",)
DEFAULT_ACTION_RULE_REASON = "action-prevented"
EFFECT_STACKING_POLICIES = ("refresh", "independent")
COMBAT_MODIFIER_PHASES = ("outgoing_damage", "attack_routine")
ATTACK_ROUTINE_WEAPON_SLOTS = ("weapon", "offhand")
ATTACK_ROUTINE_STRIKE_TARGETS = ("target", "room.secondary_hostile")
DAMAGE_ABSORB_CALCS = ("fixed", "percent_max")
DAMAGE_ABSORB_SCALING_SOURCES = (
    "health_max",
    "energy_max",
    "stamina_max",
    "attack_power",
    "ability_power",
    "armor",
    "crit",
    "dodge",
    "resilience",
    "health_regen",
    "energy_regen",
    "stamina_regen",
    "weapon_damage",
)
STAT_MODIFIER_STATS = (
    "health_max",
    "energy_max",
    "stamina_max",
    "attack_power",
    "ability_power",
    "armor",
    "crit",
    "dodge",
    "resilience",
    "health_regen",
    "energy_regen",
    "stamina_regen",
)
STAT_MODIFIER_OPS = ("add", "multiply")
STATE_COMPONENT_SCOPES = ("world", "zone", "room", "character")
STATE_COMPONENT_OPERATIONS = ("set", "increment", "clear")

ABILITY_DEFINITION_FIELDS = {
    "version",
    "command",
    "consumes_primary_action_on_resolve",
    "consumes_primary_action_while_casting",
    "target",
    "availability",
    "requirements",
    "cost",
    "cast_time",
    "cooldown",
    "help",
    "components",
    "is_active",
}


def default_ability_progression() -> dict[str, Any]:
    return {
        "max_known": DEFAULT_MAX_KNOWN_ABILITIES,
    }


def _normalize_starting_ability_entry(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, str):
        return {
            "ability": _coerce_slug(value, field_name=field_name, allow_hyphen=True),
        }
    if not isinstance(value, dict):
        raise AbilityValidationError(f"{field_name} must be an ability slug or mapping.")
    unknown_fields = sorted(set(value.keys()) - {"ability", "slug", "conditions"})
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )
    raw_slug = value.get("ability", value.get("slug"))
    normalized = {
        "ability": _coerce_slug(
            raw_slug,
            field_name=f"{field_name}.ability",
            allow_hyphen=True,
        ),
    }
    if "conditions" in value:
        conditions = deepcopy(value.get("conditions"))
        try:
            validate_condition_payload(
                conditions,
                field_name=f"{field_name}.conditions",
            )
        except ValueError as exc:
            raise AbilityValidationError(str(exc))
        normalized["conditions"] = conditions
    return normalized


def _normalize_starting_abilities(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise AbilityValidationError("ability_progression.starting_abilities must be a list.")

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_entry in enumerate(value):
        entry = _normalize_starting_ability_entry(
            raw_entry,
            field_name=f"ability_progression.starting_abilities[{index}]",
        )
        key = (entry["ability"], repr(entry.get("conditions")))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(entry)
    return normalized


def normalize_ability_progression(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise AbilityValidationError("ability_progression must be a mapping.")

    unknown_fields = sorted(set(value.keys()) - {"max_known", "starting_abilities"})
    if unknown_fields:
        raise AbilityValidationError(
            "ability_progression has unsupported field(s): "
            + ", ".join(unknown_fields)
            + "."
        )

    normalized: dict[str, Any] = {}
    raw_max_known = value.get("max_known", DEFAULT_MAX_KNOWN_ABILITIES)
    if isinstance(raw_max_known, str):
        max_known = raw_max_known.strip().lower()
        if max_known != UNCAPPED_MAX_KNOWN_ABILITIES:
            raise AbilityValidationError(
                "ability_progression.max_known must be a positive integer or uncapped."
            )
        normalized["max_known"] = UNCAPPED_MAX_KNOWN_ABILITIES
    else:
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
        normalized["max_known"] = max_known_int

    if "starting_abilities" in value:
        normalized["starting_abilities"] = _normalize_starting_abilities(
            value.get("starting_abilities")
        )
    return normalized


def max_known_abilities_for_world(world: Any) -> int | None:
    config = inherited_system_config(world)
    if config is None:
        return DEFAULT_MAX_KNOWN_ABILITIES
    progression = normalize_ability_progression(
        getattr(config, "ability_progression", None)
    )
    max_known = progression["max_known"]
    if max_known == UNCAPPED_MAX_KNOWN_ABILITIES:
        return None
    return int(max_known)


def starting_ability_slugs_for_actor(actor: Any, *, world: Any | None = None) -> list[str]:
    runtime_world = world or getattr(actor, "world", None)
    config = inherited_system_config(runtime_world)
    if config is None:
        return []
    progression = normalize_ability_progression(
        getattr(config, "ability_progression", None)
    )
    slugs: list[str] = []
    for entry in progression.get("starting_abilities", []):
        conditions = entry.get("conditions")
        if conditions and not evaluate_condition(
            conditions,
            context=ConditionContext(
                actor=actor,
                player=actor,
                world=runtime_world,
            ),
        ):
            continue
        slug = entry["ability"]
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def definition_world(world: Any) -> Any:
    if world is None:
        return None

    context_world = getattr(world, "context", None)
    if context_world is not None:
        return getattr(context_world, "instance_of", None) or context_world

    return getattr(world, "instance_of", None) or world


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
    target_range = _coerce_choice(
        value.get("range", "current_room"),
        choices=TARGET_RANGES,
        field_name="spec.target.range",
    )
    move_actor = _coerce_bool(
        value.get("move_actor", False),
        field_name="spec.target.move_actor",
    )
    opener_priority = _coerce_bool(
        value.get("opener_priority", False),
        field_name="spec.target.opener_priority",
    )
    if move_actor and target_range not in {"adjacent_room", "current_or_adjacent_room"}:
        raise AbilityValidationError(
            "spec.target.move_actor requires spec.target.range to be adjacent_room "
            "or current_or_adjacent_room."
        )
    return {
        "type": target_type,
        "default": default,
        "allow_out_of_combat": _coerce_bool(
            allow_out_of_combat,
            field_name="spec.target.allow_out_of_combat",
        ),
        "range": target_range,
        "move_actor": move_actor,
        "opener_priority": opener_priority,
    }


def normalize_ability_availability(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec.availability must be a mapping.")
    unknown_fields = sorted(set(value.keys()) - {"actors", "classes", "min_level"})
    if unknown_fields:
        raise AbilityValidationError(
            "spec.availability has unsupported field(s): "
            + ", ".join(unknown_fields)
            + "."
        )

    classes = value.get("classes", [])
    if isinstance(classes, str):
        classes = [classes]
    if not isinstance(classes, list):
        raise AbilityValidationError("spec.availability.classes must be a list.")
    normalized_classes = [
        _coerce_slug(item, field_name="spec.availability.classes[]", allow_hyphen=True)
        for item in classes
    ]
    actors = value.get("actors", list(ABILITY_ACTOR_TYPES))
    if not isinstance(actors, list):
        raise AbilityValidationError("spec.availability.actors must be a list.")
    if not actors:
        raise AbilityValidationError("spec.availability.actors must be a non-empty list.")
    normalized_actors = [
        _coerce_choice(
            item,
            choices=ABILITY_ACTOR_TYPES,
            field_name="spec.availability.actors[]",
        )
        for item in actors
    ]
    min_level = _coerce_positive_int(
        value.get("min_level", 1),
        field_name="spec.availability.min_level",
        minimum=1,
    )
    return {
        "classes": list(dict.fromkeys(normalized_classes)),
        "min_level": min_level,
        "actors": list(dict.fromkeys(normalized_actors)),
    }


def ability_allows_actor(ability_or_availability: Any, actor_type: str) -> bool:
    """Return whether an ability audience includes ``actor_type`` without I/O.

    ``ability_or_availability`` may be an ability-like object exposing an
    ``availability`` attribute or the availability mapping itself. Legacy
    availability mappings that omit ``actors`` retain the default player-and-
    mob audience. Malformed stored availability fails closed.
    """

    normalized_actor = str(actor_type or "").strip().lower()
    if normalized_actor not in ABILITY_ACTOR_TYPES:
        return False
    if ability_or_availability is None:
        return False
    availability = getattr(
        ability_or_availability,
        "availability",
        ability_or_availability,
    )
    try:
        normalized = normalize_ability_availability(availability)
    except AbilityValidationError:
        return False
    return normalized_actor in normalized["actors"]


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
    normalized = {
        "rounds": _coerce_positive_int(
            value.get("rounds", 0),
            field_name="spec.cooldown.rounds",
            minimum=0,
        )
    }
    trigger = value.get("trigger")
    if trigger not in (None, ""):
        normalized["trigger"] = _coerce_choice(
            trigger,
            choices=COOLDOWN_TRIGGERS,
            field_name="spec.cooldown.trigger",
        )
    return normalized


def _normalize_cast_time(value: Any) -> dict[str, Any]:
    if value in (None, "", {}):
        return {"rounds": 0}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec.cast_time must be a mapping.")
    unknown_fields = sorted(set(value.keys()) - {"rounds"})
    if unknown_fields:
        raise AbilityValidationError(
            "spec.cast_time has unsupported field(s): "
            + ", ".join(unknown_fields)
            + "."
        )
    return {
        "rounds": _coerce_positive_int(
            value.get("rounds", 0),
            field_name="spec.cast_time.rounds",
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


def _normalize_interrupt_component(
    value: dict[str, Any],
    *,
    field_name: str,
    default_label: str,
) -> dict[str, Any]:
    unknown_fields = sorted(
        set(value.keys()) - {"type", "target", "apply", "text"}
    )
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )
    return {
        "type": "interrupt",
        "target": _coerce_choice(
            value.get("target", "ability.target"),
            choices=INTERRUPT_TARGETS,
            field_name=f"{field_name}.target",
        ),
        "apply": _coerce_choice(
            value.get("apply", "on_resolve"),
            choices=EFFECT_APPLY_POLICIES,
            field_name=f"{field_name}.apply",
        ),
        "text": _normalize_text(value.get("text"), default_label=default_label),
    }


def _normalize_ability_help(value: Any) -> dict[str, str]:
    if value in (None, "", {}):
        return {}
    if isinstance(value, str):
        text = value.strip()
        return {"text": text} if text else {}
    if not isinstance(value, dict):
        raise AbilityValidationError("spec.help must be a string or mapping.")

    unknown_fields = sorted(set(value.keys()) - {"text"})
    if unknown_fields:
        raise AbilityValidationError(
            "spec.help has unsupported field(s): "
            + ", ".join(unknown_fields)
            + "."
        )
    text = _coerce_text(value.get("text")).strip()
    return {"text": text} if text else {}


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
    normalized = {
        "type": component_type,
        "profile": profile,
        "overrides": deepcopy(overrides),
        "text": _normalize_text(value.get("text"), default_label=default_label),
    }
    scaling = _normalize_output_scaling(
        value.get("scaling"),
        field_name=f"{field_name}.scaling",
    )
    if scaling:
        normalized["scaling"] = scaling
    return normalized


def _normalize_output_scaling(value: Any, *, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise AbilityValidationError(f"{field_name} must be a mapping.")

    unknown_fields = sorted(set(value.keys()) - {"from", "multiplier_per_point", "max_points"})
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )

    source = str(value.get("from") or "").strip()
    if not source.startswith("state."):
        raise AbilityValidationError(f"{field_name}.from must be a state.* path.")
    normalized = {
        "from": source,
        "multiplier_per_point": _coerce_number(
            value.get("multiplier_per_point", 0),
            field_name=f"{field_name}.multiplier_per_point",
        ),
    }
    if "max_points" in value:
        normalized["max_points"] = _coerce_number(
            value.get("max_points"),
            field_name=f"{field_name}.max_points",
            minimum=0,
        )
    return normalized


def _normalize_state_component(
    value: dict[str, Any],
    *,
    field_name: str,
    default_label: str,
) -> dict[str, Any]:
    operation = _coerce_choice(
        value.get("op", value.get("operation", "increment")),
        choices=STATE_COMPONENT_OPERATIONS,
        field_name=f"{field_name}.op",
    )
    normalized: dict[str, Any] = {
        "type": "state",
        "scope": _coerce_choice(
            value.get("scope", "character"),
            choices=STATE_COMPONENT_SCOPES,
            field_name=f"{field_name}.scope",
        ),
        "key": _coerce_text(value.get("key")).strip(),
        "op": operation,
        "apply": _coerce_choice(
            value.get("apply", "on_resolve"),
            choices=EFFECT_APPLY_POLICIES,
            field_name=f"{field_name}.apply",
        ),
        "text": _normalize_text(value.get("text"), default_label=default_label),
    }
    if not normalized["key"]:
        raise AbilityValidationError(f"{field_name}.key cannot be empty.")
    if operation == "set":
        normalized["value"] = deepcopy(value.get("value"))
    elif operation == "increment":
        normalized["amount"] = _coerce_number(
            value.get("amount", 1),
            field_name=f"{field_name}.amount",
        )
        if "min" in value:
            normalized["min"] = _coerce_number(
                value.get("min"),
                field_name=f"{field_name}.min",
            )
        if "max" in value:
            normalized["max"] = _coerce_number(
                value.get("max"),
                field_name=f"{field_name}.max",
            )
        if "min" in normalized and "max" in normalized and normalized["min"] > normalized["max"]:
            raise AbilityValidationError(f"{field_name}.min must be <= {field_name}.max.")
    return normalized


def _duration_rounds(value: Any, *, field_name: str) -> int:
    if isinstance(value, dict):
        value = value.get("rounds")
    return _coerce_positive_int(value, field_name=field_name, minimum=1)


def _normalize_effect_component(
    value: dict[str, Any],
    *,
    field_name: str,
    default_label: str,
    ability_target: dict[str, Any],
) -> dict[str, Any]:
    raw_effect = value.get("effect")
    effect_type = _coerce_slug(
        raw_effect,
        field_name=f"{field_name}.effect",
        allow_hyphen=True,
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

    raw_primitives = value.get("primitives") or []
    primitive_types = {
        str(primitive.get("type") or "").strip().lower()
        for primitive in raw_primitives
        if isinstance(primitive, dict)
    }
    target_selector = str(value.get("target") or "ability.target").strip().lower()
    out_of_combat_self_barrier = (
        "damage_absorb" in primitive_types
        and bool(ability_target.get("allow_out_of_combat"))
        and (
            target_selector in {"actor", "self", "effect.source"}
            or (
                target_selector == "ability.target"
                and ability_target.get("type") in {"self", "ally"}
            )
        )
    )
    default_scope = (
        "character"
        if effect_type in {"dot", "hot"}
        or "tick" in value
        or target_selector in {"room.allies", "room.players"}
        or bool(primitive_types & {"combat_modifier", "stat_modifier"})
        or out_of_combat_self_barrier
        else "encounter"
    )
    normalized: dict[str, Any] = {
        "type": "effect",
        "effect": effect_type,
        "scope": _coerce_choice(
            value.get("scope", default_scope),
            choices=EFFECT_SCOPES,
            field_name=f"{field_name}.scope",
        ),
        "category": _coerce_choice(
            value.get("category", "neutral"),
            choices=EFFECT_CATEGORIES,
            field_name=f"{field_name}.category",
        ),
        "target": _coerce_choice(
            value.get("target", "ability.target"),
            choices=EFFECT_TARGETS,
            field_name=f"{field_name}.target",
        ),
        "duration": {"rounds": duration_rounds},
        "apply": apply_policy,
        "text": _normalize_text(value.get("text"), default_label=default_label),
    }
    stack_key = str(value.get("stack_key") or "").strip().lower()
    if stack_key:
        normalized["stack_key"] = _coerce_slug(
            stack_key,
            field_name=f"{field_name}.stack_key",
            allow_hyphen=True,
        )
    if "stacking" in value:
        normalized["stacking"] = _coerce_choice(
            value.get("stacking"),
            choices=EFFECT_STACKING_POLICIES,
            field_name=f"{field_name}.stacking",
        )
    primitives = _normalize_effect_primitives(
        value.get("primitives"),
        field_name=f"{field_name}.primitives",
    )
    if primitives:
        normalized["primitives"] = primitives

    if effect_type in {"dot", "hot"} or "tick" in value:
        tick = value.get("tick") or {}
        if not isinstance(tick, dict):
            raise AbilityValidationError(f"{field_name}.tick must be a mapping.")
        normalized_tick: dict[str, Any] = {
            "phase": _coerce_choice(
                tick.get("phase", "round_start"),
                choices=EFFECT_TICK_PHASES,
                field_name=f"{field_name}.tick.phase",
            ),
            "every_rounds": _coerce_positive_int(
                tick.get("every_rounds", 1),
                field_name=f"{field_name}.tick.every_rounds",
                minimum=1,
            )
        }
        tick_primitives = _normalize_effect_primitives(
            tick.get("primitives"),
            field_name=f"{field_name}.tick.primitives",
        )
        if tick_primitives:
            normalized_tick["primitives"] = tick_primitives
        else:
            tick_component = tick.get("component")
            if not isinstance(tick_component, dict):
                raise AbilityValidationError(f"{field_name}.tick.component must be a mapping.")
            expected_type = "damage" if effect_type == "dot" else "healing"
            normalized_tick["component"] = _normalize_output_component(
                {**tick_component, "type": expected_type},
                field_name=f"{field_name}.tick.component",
                component_type=expected_type,
                default_label=default_label,
            )
        normalized["tick"] = normalized_tick
    return normalized


def _normalize_effect_primitives(value: Any, *, field_name: str) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise AbilityValidationError(f"{field_name} must be a list.")
    return [
        _normalize_effect_primitive(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(value)
    ]


def _normalize_effect_primitive(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AbilityValidationError(f"{field_name} must be a mapping.")
    primitive_type = _coerce_choice(
        value.get("type"),
        choices=EFFECT_PRIMITIVE_TYPES,
        field_name=f"{field_name}.type",
    )
    if primitive_type == "resource_change":
        return _normalize_resource_change_primitive(value, field_name=field_name)
    if primitive_type == "damage_absorb":
        return _normalize_damage_absorb_primitive(value, field_name=field_name)
    if primitive_type == "combat_modifier":
        return _normalize_combat_modifier_primitive(value, field_name=field_name)
    if primitive_type == "stat_modifier":
        return _normalize_stat_modifier_primitive(value, field_name=field_name)
    if primitive_type == "action_rule":
        return _normalize_action_rule_primitive(value, field_name=field_name)
    return _normalize_proc_primitive(value, field_name=field_name)


def _normalize_action_rule_primitive(
    value: dict[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    unknown_fields = sorted(
        set(value.keys()) - {"type", "phase", "rule", "actions", "reason"}
    )
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )

    phase = value.get("phase")
    if not isinstance(phase, str):
        raise AbilityValidationError(f"{field_name}.phase must be a string.")
    rule = value.get("rule")
    if not isinstance(rule, str):
        raise AbilityValidationError(f"{field_name}.rule must be a string.")

    actions = value.get("actions")
    if not isinstance(actions, list) or not actions:
        raise AbilityValidationError(f"{field_name}.actions must be a non-empty list.")
    normalized_actions: list[str] = []
    for index, action in enumerate(actions):
        if not isinstance(action, str):
            raise AbilityValidationError(
                f"{field_name}.actions[{index}] must be a string."
            )
        normalized_action = _coerce_choice(
            action,
            choices=ACTION_RULE_ACTIONS,
            field_name=f"{field_name}.actions[{index}]",
        )
        if normalized_action not in normalized_actions:
            normalized_actions.append(normalized_action)

    reason = value.get("reason", DEFAULT_ACTION_RULE_REASON)
    if not isinstance(reason, str):
        raise AbilityValidationError(f"{field_name}.reason must be a string.")
    return {
        "type": "action_rule",
        "phase": _coerce_choice(
            phase,
            choices=ACTION_RULE_PHASES,
            field_name=f"{field_name}.phase",
        ),
        "rule": _coerce_choice(
            rule,
            choices=ACTION_RULE_RULES,
            field_name=f"{field_name}.rule",
        ),
        "actions": normalized_actions,
        "reason": _coerce_slug(
            reason,
            field_name=f"{field_name}.reason",
            allow_hyphen=True,
        ),
    }


def _normalize_combat_modifier_primitive(value: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    unknown_fields = sorted(set(value.keys()) - {"type", "phase", "multiplier", "attack_routine"})
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )
    phase = _coerce_choice(
        value.get("phase"),
        choices=COMBAT_MODIFIER_PHASES,
        field_name=f"{field_name}.phase",
    )
    if phase == "attack_routine":
        return {
            "type": "combat_modifier",
            "phase": phase,
            "attack_routine": _normalize_attack_routine_modifier(
                value.get("attack_routine"),
                field_name=f"{field_name}.attack_routine",
            ),
        }
    return {
        "type": "combat_modifier",
        "phase": phase,
        "multiplier": _coerce_number(
            value.get("multiplier", 1),
            field_name=f"{field_name}.multiplier",
            minimum=0,
        ),
    }


def _normalize_attack_routine_modifier(value: Any, *, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise AbilityValidationError(f"{field_name} must be a mapping.")
    unknown_fields = sorted(set(value.keys()) - {"extra_mainhand_strikes", "strike"})
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )
    normalized: dict[str, Any] = {}
    if "extra_mainhand_strikes" in value:
        normalized["extra_mainhand_strikes"] = _coerce_positive_int(
            value.get("extra_mainhand_strikes"),
            field_name=f"{field_name}.extra_mainhand_strikes",
            minimum=0,
        )
    strike = value.get("strike") or {}
    if strike not in ({}, None):
        if not isinstance(strike, dict):
            raise AbilityValidationError(f"{field_name}.strike must be a mapping.")
        unknown_strike = sorted(
            set(strike.keys()) - {
                "source",
                "target",
                "weapon_slot",
                "damage_multiplier",
                "label",
            }
        )
        if unknown_strike:
            raise AbilityValidationError(
                f"{field_name}.strike has unsupported field(s): {', '.join(unknown_strike)}."
            )
        normalized_strike: dict[str, Any] = {}
        if "source" in strike:
            normalized_strike["source"] = _coerce_slug(
                strike.get("source"),
                field_name=f"{field_name}.strike.source",
            )
        if "target" in strike:
            normalized_strike["target"] = _coerce_choice(
                strike.get("target", "target"),
                choices=ATTACK_ROUTINE_STRIKE_TARGETS,
                field_name=f"{field_name}.strike.target",
            )
        if "weapon_slot" in strike:
            normalized_strike["weapon_slot"] = _coerce_choice(
                strike.get("weapon_slot"),
                choices=ATTACK_ROUTINE_WEAPON_SLOTS,
                field_name=f"{field_name}.strike.weapon_slot",
            )
        if "damage_multiplier" in strike:
            normalized_strike["damage_multiplier"] = _coerce_number(
                strike.get("damage_multiplier"),
                field_name=f"{field_name}.strike.damage_multiplier",
                minimum=0,
            )
        if "label" in strike:
            normalized_strike["label"] = str(strike.get("label") or "").strip()
        normalized["strike"] = normalized_strike
    return normalized


def _normalize_resource_change_primitive(value: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    unknown_fields = sorted(set(value.keys()) - {"type", "resource", "amount", "calc", "target"})
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )
    return {
        "type": "resource_change",
        "resource": _coerce_choice(
            value.get("resource"),
            choices=COST_RESOURCES,
            field_name=f"{field_name}.resource",
        ),
        "amount": _coerce_number(
            value.get("amount", 0),
            field_name=f"{field_name}.amount",
        ),
        "calc": _coerce_choice(
            value.get("calc", "fixed"),
            choices=COST_CALCS,
            field_name=f"{field_name}.calc",
        ),
        "target": _coerce_choice(
            value.get("target", "effect.target"),
            choices=EFFECT_TARGETS,
            field_name=f"{field_name}.target",
        ),
    }


def _normalize_stat_modifier_primitive(value: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    unknown_fields = sorted(set(value.keys()) - {"type", "stat", "op", "amount", "multiplier"})
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )

    raw_op = value.get("op")
    if raw_op in (None, ""):
        raw_op = "multiply" if "multiplier" in value and "amount" not in value else "add"
    op = _coerce_choice(
        raw_op,
        choices=STAT_MODIFIER_OPS,
        field_name=f"{field_name}.op",
    )
    stat = _coerce_choice(
        value.get("stat"),
        choices=STAT_MODIFIER_STATS,
        field_name=f"{field_name}.stat",
    )
    normalized = {
        "type": "stat_modifier",
        "stat": stat,
        "op": op,
    }
    if op == "add":
        if "multiplier" in value:
            raise AbilityValidationError(
                f"{field_name}.multiplier is only supported for op multiply."
            )
        normalized["amount"] = _coerce_number(
            value.get("amount", 0),
            field_name=f"{field_name}.amount",
        )
        return normalized

    if "amount" in value:
        raise AbilityValidationError(
            f"{field_name}.amount is only supported for op add."
        )
    normalized["multiplier"] = _coerce_number(
        value.get("multiplier", 1),
        field_name=f"{field_name}.multiplier",
        minimum=0,
    )
    return normalized


def _normalize_damage_absorb_types(value: Any, *, field_name: str) -> list[str]:
    if value in (None, "", "all"):
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise AbilityValidationError(f"{field_name} must be a list.")

    normalized: list[str] = []
    for index, raw_type in enumerate(value):
        damage_type = _coerce_slug(
            raw_type,
            field_name=f"{field_name}[{index}]",
            allow_hyphen=True,
        )
        if damage_type == "all":
            return []
        if damage_type not in normalized:
            normalized.append(damage_type)
    return normalized


def _normalize_damage_absorb_scaling(value: Any, *, field_name: str) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise AbilityValidationError(f"{field_name} must be a list.")

    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(value):
        item_field = f"{field_name}[{index}]"
        if not isinstance(entry, dict):
            raise AbilityValidationError(f"{item_field} must be a mapping.")
        unknown_fields = sorted(set(entry.keys()) - {"source", "multiplier"})
        if unknown_fields:
            raise AbilityValidationError(
                f"{item_field} has unsupported field(s): {', '.join(unknown_fields)}."
            )
        normalized.append(
            {
                "source": _coerce_choice(
                    entry.get("source"),
                    choices=DAMAGE_ABSORB_SCALING_SOURCES,
                    field_name=f"{item_field}.source",
                ),
                "multiplier": _coerce_number(
                    entry.get("multiplier", 0),
                    field_name=f"{item_field}.multiplier",
                ),
            }
        )
    return normalized


def _normalize_damage_absorb_primitive(value: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    unknown_fields = sorted(
        set(value.keys()) - {"type", "amount", "calc", "damage_type", "damage_types", "scaling"}
    )
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )
    if "damage_type" in value and "damage_types" in value:
        raise AbilityValidationError(
            f"{field_name} cannot define both damage_type and damage_types."
        )
    damage_types = value.get("damage_types", value.get("damage_type"))
    normalized = {
        "type": "damage_absorb",
        "amount": _coerce_number(
            value.get("amount", 0),
            field_name=f"{field_name}.amount",
            minimum=0,
        ),
        "calc": _coerce_choice(
            value.get("calc", "fixed"),
            choices=DAMAGE_ABSORB_CALCS,
            field_name=f"{field_name}.calc",
        ),
        "damage_types": _normalize_damage_absorb_types(
            damage_types,
            field_name=f"{field_name}.damage_types",
        ),
    }
    scaling = _normalize_damage_absorb_scaling(
        value.get("scaling"),
        field_name=f"{field_name}.scaling",
    )
    if scaling:
        normalized["scaling"] = scaling
    return normalized


def _normalize_proc_primitive(value: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    unknown_fields = sorted(set(value.keys()) - {"type", "phase", "conditions", "actions"})
    if unknown_fields:
        raise AbilityValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )
    conditions = deepcopy(value.get("conditions") or {})
    try:
        validate_condition_payload(conditions, field_name=f"{field_name}.conditions")
    except ValueError as exc:
        raise AbilityValidationError(str(exc))
    actions = value.get("actions")
    if not isinstance(actions, list) or not actions:
        raise AbilityValidationError(f"{field_name}.actions must be a non-empty list.")
    normalized_actions: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise AbilityValidationError(f"{field_name}.actions[{index}] must be a mapping.")
        action_type = str(action.get("type") or "").strip().lower()
        if action_type != "resource_change":
            raise AbilityValidationError(f"{field_name}.actions[{index}].type must be resource_change.")
        normalized_actions.append(
            _normalize_resource_change_primitive(
                action,
                field_name=f"{field_name}.actions[{index}]",
            )
        )
    return {
        "type": "proc",
        "phase": _coerce_choice(
            value.get("phase"),
            choices=EFFECT_PROC_PHASES,
            field_name=f"{field_name}.phase",
        ),
        "conditions": conditions,
        "actions": normalized_actions,
    }


def _normalize_component(
    value: Any,
    *,
    field_name: str,
    default_label: str,
    ability_target: dict[str, Any],
) -> dict[str, Any]:
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
    if component_type == "state":
        return _normalize_state_component(
            value,
            field_name=field_name,
            default_label=default_label,
        )
    if component_type == "interrupt":
        return _normalize_interrupt_component(
            value,
            field_name=field_name,
            default_label=default_label,
        )
    return _normalize_effect_component(
        value,
        field_name=field_name,
        default_label=default_label,
        ability_target=ability_target,
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
    target = _normalize_target(value.get("target"))
    normalized_components = [
        _normalize_component(
            component,
            field_name=f"spec.components[{index}]",
            default_label=label,
            ability_target=target,
        )
        for index, component in enumerate(components)
    ]
    if (
        target["type"] != "hostile"
        and any(component["type"] == "interrupt" for component in normalized_components)
    ):
        raise AbilityValidationError(
            "spec.components interrupt entries require spec.target.type to be hostile."
        )
    return {
        "version": version,
        "command": _normalize_command(value.get("command"), slug=slug),
        "consumes_primary_action_on_resolve": _coerce_bool(
            value.get("consumes_primary_action_on_resolve", True),
            field_name="spec.consumes_primary_action_on_resolve",
        ),
        "consumes_primary_action_while_casting": _coerce_bool(
            value.get("consumes_primary_action_while_casting", True),
            field_name="spec.consumes_primary_action_while_casting",
        ),
        "target": target,
        "availability": normalize_ability_availability(value.get("availability")),
        "requirements": _normalize_requirements(value.get("requirements")),
        "cost": _normalize_cost(value.get("cost")),
        "cast_time": _normalize_cast_time(value.get("cast_time")),
        "cooldown": _normalize_cooldown(value.get("cooldown")),
        "help": _normalize_ability_help(value.get("help")),
        "components": normalized_components,
        "is_active": _coerce_bool(
            value.get("is_active", True),
            field_name="spec.is_active",
        ),
    }
