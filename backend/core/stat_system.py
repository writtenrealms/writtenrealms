"""
World-authored stat system support for WR2.

This module keeps the engine contract small and stable while allowing worlds
to customize:

- attribute definitions, labels, and ordering
- class/archetype attribute weight profiles
- formula coefficients that map attributes into stats
- player-facing labels for resources and derived stats

Runtime names intentionally use WR2 terminology:

- energy / energy_max / energy_regen
- ability_power
"""
from __future__ import annotations

from copy import deepcopy
import math
from collections.abc import Iterable
from typing import Any

from config import constants
from config import game_settings as config


class StatSystemValidationError(ValueError):
    pass


CANONICAL_RESOURCE_LABEL_KEYS = ("health", "energy", "stamina")
CANONICAL_DERIVED_LABEL_KEYS = (
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

CANONICAL_COMPUTED_STAT_KEYS = (
    "health_max",
    "energy_base",
    "energy_max",
    "energy_regen",
    "stamina_base",
    "stamina_max",
    "stamina_regen",
    "health_base",
    "health_regen",
    "attack_power",
    "ability_power",
    "armor",
    "crit",
    "dodge",
    "resilience",
)

DIRECT_STAT_KEYS = (
    "health_max",
    "energy_max",
    "energy_regen",
    "stamina_max",
    "stamina_regen",
    "health_regen",
    "attack_power",
    "ability_power",
    "armor",
    "crit",
    "dodge",
    "resilience",
)

DEFAULT_STAT_SYSTEM = {
    "attributes": [],
    "labels": {
        "resources": {
            "health": "Health",
            "energy": "Energy",
            "stamina": "Stamina",
        },
        "derived": {
            "attack_power": "Attack Power",
            "ability_power": "Ability Power",
            "armor": "Armor",
            "crit": "Crit",
            "dodge": "Dodge",
            "resilience": "Resilience",
            "health_regen": "Health Regen",
            "energy_regen": "Energy Regen",
            "stamina_regen": "Stamina Regen",
        },
        "classes": {},
    },
    "derived_display_order": [
        "attack_power",
        "ability_power",
        "crit",
        "armor",
        "resilience",
        "dodge",
        "health_regen",
        "energy_regen",
        "stamina_regen",
    ],
    "default_profile": {
        "label": "",
        "main_attribute": "",
        "attribute_weights": {},
        "derived_rules": [],
    },
    "class_profiles": {},
    "formulas": {
        "base_resources": {
            "energy": {},
            "stamina": {},
            "health": {},
        },
        "global_rules": [],
        "two_handed_multipliers": {
            "attack_power": 1.5,
            "ability_power": 1.5,
        },
        "mob_boost": {
            "slot_factor": 10.25,
            "elite_multiplier": 1.2,
            "armor_multiplier_by_profile": {
                "default": 2,
            },
        },
    },
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coerce_label_map(
    value: Any,
    *,
    field_name: str,
    allowed_keys: tuple[str, ...] | None = None,
) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise StatSystemValidationError(f"{field_name} must be a mapping.")

    normalized: dict[str, str] = {}
    for raw_key, raw_label in value.items():
        key = str(raw_key if raw_key is not None else "").strip()
        if not key:
            raise StatSystemValidationError(
                f"{field_name} keys must be non-empty strings."
            )
        if allowed_keys and key not in allowed_keys:
            raise StatSystemValidationError(
                f"{field_name}.{key} is not supported."
            )
        fallback_label = key.replace("_", " ").title()
        normalized[key] = str(raw_label or "").strip() or fallback_label
    return normalized


def _coerce_attributes(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return deepcopy(DEFAULT_STAT_SYSTEM["attributes"])
    if not isinstance(value, list):
        raise StatSystemValidationError(
            "stats.attributes must be a list."
        )

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise StatSystemValidationError(
                f"stats.attributes[{index}] must be a mapping."
            )
        key = str(entry.get("key") or "").strip()
        if not key:
            raise StatSystemValidationError(
                f"stats.attributes[{index}].key is required."
            )
        if key in seen:
            raise StatSystemValidationError(
                f"stats.attributes contains duplicate key '{key}'."
            )
        seen.add(key)
        label = str(entry.get("label") or "").strip() or key.replace("_", " ").title()
        normalized.append(
            {
                "key": key,
                "label": label,
            }
        )
    return normalized


def _coerce_rule(
    value: Any,
    *,
    field_name: str,
    allowed_sources: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StatSystemValidationError(f"{field_name} must be a mapping.")

    source = str(value.get("source") or "").strip()
    target = str(value.get("target") or "").strip()
    multiplier = value.get("multiplier")
    mode = str(value.get("mode") or "total").strip()

    if source not in allowed_sources:
        raise StatSystemValidationError(
            f"{field_name}.source must reference a declared attribute."
        )
    if target not in CANONICAL_COMPUTED_STAT_KEYS:
        raise StatSystemValidationError(
            f"{field_name}.target must be a supported stat."
        )
    if not _is_number(multiplier):
        raise StatSystemValidationError(
            f"{field_name}.multiplier must be a number."
        )
    if mode not in ("total", "base_only", "bonus_from_total_minus_base"):
        raise StatSystemValidationError(
            f"{field_name}.mode must be one of total, base_only, bonus_from_total_minus_base."
        )

    return {
        "source": source,
        "target": target,
        "multiplier": float(multiplier),
        "mode": mode,
    }


def _coerce_rule_list(
    value: Any,
    *,
    field_name: str,
    allowed_sources: set[str],
) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise StatSystemValidationError(f"{field_name} must be a list.")
    return [
        _coerce_rule(
            entry,
            field_name=f"{field_name}[{index}]",
            allowed_sources=allowed_sources,
        )
        for index, entry in enumerate(value)
    ]


def _coerce_weights(
    value: Any,
    *,
    field_name: str,
    attribute_keys: list[str],
) -> dict[str, float]:
    if value in (None, ""):
        return {key: 0.0 for key in attribute_keys}
    if not isinstance(value, dict):
        raise StatSystemValidationError(f"{field_name} must be a mapping.")

    normalized: dict[str, float] = {key: 0.0 for key in attribute_keys}
    for key, raw_value in value.items():
        stat_key = str(key or "").strip()
        if stat_key not in normalized:
            raise StatSystemValidationError(
                f"{field_name}.{stat_key} must reference a declared attribute."
            )
        if not _is_number(raw_value):
            raise StatSystemValidationError(
                f"{field_name}.{stat_key} must be a number."
            )
        normalized[stat_key] = float(raw_value)
    return normalized


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _coerce_profile(
    raw_profile: Any,
    *,
    field_name: str,
    attribute_keys: list[str],
    default_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if raw_profile in (None, ""):
        raw_profile = {}
    if not isinstance(raw_profile, dict):
        raise StatSystemValidationError(f"{field_name} must be a mapping.")

    base_profile = deepcopy(default_profile or {})
    allowed_profile_keys = {
        "label",
        "main_attribute",
        "attribute_weights",
        "derived_rules",
    }
    unknown_profile_keys = sorted(set(raw_profile.keys()) - allowed_profile_keys)
    if unknown_profile_keys:
        raise StatSystemValidationError(
            f"Unsupported {field_name} field(s): {', '.join(unknown_profile_keys)}."
        )

    label = str(raw_profile.get("label") or base_profile.get("label") or "").strip()
    main_attribute = str(
        raw_profile.get("main_attribute")
        if "main_attribute" in raw_profile
        else base_profile.get("main_attribute") or ""
    ).strip()
    if main_attribute and main_attribute not in attribute_keys:
        raise StatSystemValidationError(
            f"{field_name}.main_attribute must reference a declared attribute."
        )

    weights = _coerce_weights(
        raw_profile.get("attribute_weights", base_profile.get("attribute_weights")),
        field_name=f"{field_name}.attribute_weights",
        attribute_keys=attribute_keys,
    )
    rules = _coerce_rule_list(
        raw_profile.get("derived_rules", base_profile.get("derived_rules")),
        field_name=f"{field_name}.derived_rules",
        allowed_sources=set(attribute_keys),
    )

    return {
        "label": label,
        "main_attribute": main_attribute,
        "attribute_weights": weights,
        "derived_rules": rules,
    }


def _coerce_base_resource_config(
    value: Any,
    *,
    field_name: str,
    attribute_keys: list[str],
    default_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = deepcopy(value if value is not None else default_value or {})
    if not isinstance(value, dict):
        raise StatSystemValidationError(f"{field_name} must be a mapping.")

    flat = value.get("flat")
    source = str(value.get("source") or "").strip()
    multiplier = value.get("multiplier")

    if flat is not None:
        if not _is_number(flat):
            raise StatSystemValidationError(f"{field_name}.flat must be a number.")
        return {"flat": float(flat)}

    if source:
        if source not in attribute_keys:
            raise StatSystemValidationError(
                f"{field_name}.source must reference a declared attribute."
            )
        if not _is_number(multiplier):
            raise StatSystemValidationError(
                f"{field_name}.multiplier must be a number."
            )
        return {
            "source": source,
            "multiplier": float(multiplier),
        }

    if multiplier is not None:
        raise StatSystemValidationError(
            f"{field_name}.source is required when multiplier is set."
        )
    return {}


def normalize_stat_system(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise StatSystemValidationError("stats must be a mapping.")
    allowed_top_level_keys = {
        "attributes",
        "labels",
        "derived_display_order",
        "default_profile",
        "class_profiles",
        "formulas",
    }
    unknown_keys = sorted(set(value.keys()) - allowed_top_level_keys)
    if unknown_keys:
        raise StatSystemValidationError(
            f"Unsupported stats field(s): {', '.join(unknown_keys)}."
        )

    normalized = deepcopy(DEFAULT_STAT_SYSTEM)

    attributes = _coerce_attributes(value.get("attributes"))
    attribute_keys = [entry["key"] for entry in attributes]
    normalized["attributes"] = attributes

    raw_labels = value.get("labels") or {}
    if raw_labels not in ({}, None) and not isinstance(raw_labels, dict):
        raise StatSystemValidationError("stats.labels must be a mapping.")
    raw_class_labels = _coerce_label_map(
        (raw_labels or {}).get("classes"),
        field_name="stats.labels.classes",
        allowed_keys=None,
    )
    normalized["labels"]["resources"] = _merge_dict(
        normalized["labels"]["resources"],
        _coerce_label_map(
            (raw_labels or {}).get("resources"),
            field_name="stats.labels.resources",
            allowed_keys=CANONICAL_RESOURCE_LABEL_KEYS,
        ),
    )
    normalized["labels"]["derived"] = _merge_dict(
        normalized["labels"]["derived"],
        _coerce_label_map(
            (raw_labels or {}).get("derived"),
            field_name="stats.labels.derived",
            allowed_keys=CANONICAL_DERIVED_LABEL_KEYS,
        ),
    )
    class_profiles_declared = "class_profiles" in value
    class_labels_declared = "classes" in raw_labels
    if class_labels_declared:
        normalized["labels"]["classes"] = raw_class_labels
    elif class_profiles_declared:
        normalized["labels"]["classes"] = {}
    else:
        normalized["labels"]["classes"] = _merge_dict(
            normalized["labels"]["classes"],
            raw_class_labels,
        )

    display_order = value.get("derived_display_order")
    if display_order is not None:
        if not isinstance(display_order, list) or not display_order:
            raise StatSystemValidationError(
                "stats.derived_display_order must be a non-empty list."
            )
        normalized_order: list[str] = []
        seen: set[str] = set()
        allowed = set(CANONICAL_DERIVED_LABEL_KEYS)
        for index, raw_entry in enumerate(display_order):
            entry = str(raw_entry or "").strip()
            if entry not in allowed:
                raise StatSystemValidationError(
                    f"stats.derived_display_order[{index}] is not a supported derived stat."
                )
            if entry in seen:
                continue
            seen.add(entry)
            normalized_order.append(entry)
        normalized["derived_display_order"] = normalized_order

    normalized["default_profile"] = _coerce_profile(
        value.get("default_profile"),
        field_name="stats.default_profile",
        attribute_keys=attribute_keys,
        default_profile=normalized["default_profile"],
    )

    raw_class_profiles = value.get("class_profiles") or {}
    if raw_class_profiles not in ({}, None) and not isinstance(raw_class_profiles, dict):
        raise StatSystemValidationError("stats.class_profiles must be a mapping.")
    class_profiles = {} if class_profiles_declared else deepcopy(normalized["class_profiles"])
    for profile_key, profile_value in raw_class_profiles.items():
        normalized_key = str(profile_key or "").strip()
        if not normalized_key:
            raise StatSystemValidationError(
                "stats.class_profiles keys must be non-empty strings."
            )
        class_profiles[normalized_key] = _coerce_profile(
            profile_value,
            field_name=f"stats.class_profiles.{normalized_key}",
            attribute_keys=attribute_keys,
            default_profile=class_profiles.get(normalized_key),
        )
        if class_profiles[normalized_key]["label"]:
            normalized["labels"]["classes"][normalized_key] = class_profiles[normalized_key]["label"]
    normalized["class_profiles"] = class_profiles
    formulas = deepcopy(normalized["formulas"])
    raw_formulas = value.get("formulas") or {}
    if raw_formulas not in ({}, None) and not isinstance(raw_formulas, dict):
        raise StatSystemValidationError("stats.formulas must be a mapping.")

    raw_base_resources = (raw_formulas or {}).get("base_resources") or {}
    if raw_base_resources not in ({}, None) and not isinstance(raw_base_resources, dict):
        raise StatSystemValidationError("stats.formulas.base_resources must be a mapping.")
    base_resources = deepcopy(formulas["base_resources"])
    for resource_key in CANONICAL_RESOURCE_LABEL_KEYS:
        base_resources[resource_key] = _coerce_base_resource_config(
            raw_base_resources.get(resource_key),
            field_name=f"stats.formulas.base_resources.{resource_key}",
            attribute_keys=attribute_keys,
            default_value=base_resources.get(resource_key),
        )
    formulas["base_resources"] = base_resources

    formulas["global_rules"] = _coerce_rule_list(
        raw_formulas.get("global_rules", formulas.get("global_rules")),
        field_name="stats.formulas.global_rules",
        allowed_sources=set(attribute_keys),
    )

    raw_two_handed = raw_formulas.get("two_handed_multipliers")
    if raw_two_handed is not None:
        if not isinstance(raw_two_handed, dict):
            raise StatSystemValidationError(
                "stats.formulas.two_handed_multipliers must be a mapping."
            )
        normalized_two_handed = {}
        for stat_key, raw_multiplier in raw_two_handed.items():
            normalized_key = str(stat_key or "").strip()
            if normalized_key not in ("attack_power", "ability_power"):
                raise StatSystemValidationError(
                    "stats.formulas.two_handed_multipliers only supports attack_power and ability_power."
                )
            if not _is_number(raw_multiplier):
                raise StatSystemValidationError(
                    f"stats.formulas.two_handed_multipliers.{normalized_key} must be a number."
                )
            normalized_two_handed[normalized_key] = float(raw_multiplier)
        formulas["two_handed_multipliers"] = _merge_dict(
            formulas["two_handed_multipliers"],
            normalized_two_handed,
        )

    raw_mob_boost = raw_formulas.get("mob_boost")
    if raw_mob_boost is not None:
        if not isinstance(raw_mob_boost, dict):
            raise StatSystemValidationError("stats.formulas.mob_boost must be a mapping.")
        mob_boost = deepcopy(formulas["mob_boost"])
        for key in ("slot_factor", "elite_multiplier"):
            if key in raw_mob_boost:
                if not _is_number(raw_mob_boost[key]):
                    raise StatSystemValidationError(
                        f"stats.formulas.mob_boost.{key} must be a number."
                    )
                mob_boost[key] = float(raw_mob_boost[key])
        armor_map = raw_mob_boost.get("armor_multiplier_by_profile")
        if armor_map is not None:
            if not isinstance(armor_map, dict):
                raise StatSystemValidationError(
                    "stats.formulas.mob_boost.armor_multiplier_by_profile must be a mapping."
                )
            normalized_armor_map: dict[str, float] = {}
            for raw_key, raw_multiplier in armor_map.items():
                profile_key = str(raw_key or "").strip()
                if not profile_key:
                    raise StatSystemValidationError(
                        "stats.formulas.mob_boost.armor_multiplier_by_profile keys must be non-empty strings."
                    )
                if not _is_number(raw_multiplier):
                    raise StatSystemValidationError(
                        f"stats.formulas.mob_boost.armor_multiplier_by_profile.{profile_key} must be a number."
                    )
                normalized_armor_map[profile_key] = float(raw_multiplier)
            mob_boost["armor_multiplier_by_profile"] = _merge_dict(
                mob_boost["armor_multiplier_by_profile"],
                normalized_armor_map,
            )
        formulas["mob_boost"] = mob_boost

    normalized["formulas"] = formulas
    return normalized


def get_world_stat_system(world) -> dict[str, Any]:
    if world is None:
        return deepcopy(DEFAULT_STAT_SYSTEM)
    effective_config = getattr(world, "effective_config", None)
    config_obj = effective_config or getattr(world, "config", None)
    if config_obj is None:
        return deepcopy(DEFAULT_STAT_SYSTEM)
    return normalize_stat_system(getattr(config_obj, "stat_system", None))


def world_uses_classes(world) -> bool:
    try:
        stat_system = get_world_stat_system(world)
    except StatSystemValidationError:
        return False
    return bool(stat_system.get("class_profiles"))


def get_attribute_order(stat_system: dict[str, Any]) -> list[str]:
    return [entry["key"] for entry in stat_system["attributes"]]


def get_world_label_bundle(world) -> dict[str, Any]:
    stat_system = get_world_stat_system(world)
    return {
        "resources": deepcopy(stat_system["labels"]["resources"]),
        "attributes": {
            entry["key"]: entry["label"]
            for entry in stat_system["attributes"]
        },
        "derived": deepcopy(stat_system["labels"]["derived"]),
        "classes": deepcopy(stat_system["labels"]["classes"]),
        "order": {
            "resources": list(CANONICAL_RESOURCE_LABEL_KEYS),
            "attributes": get_attribute_order(stat_system),
            "derived": list(stat_system["derived_display_order"]),
        },
    }


def _source_value_for_rule(
    *,
    rule: dict[str, Any],
    total_attributes: dict[str, float],
    own_attributes: dict[str, float],
) -> float:
    source = rule["source"]
    mode = rule["mode"]
    total_value = float(total_attributes.get(source, 0.0) or 0.0)
    base_value = float(own_attributes.get(source, 0.0) or 0.0)
    if mode == "base_only":
        return base_value
    if mode == "bonus_from_total_minus_base":
        return max(0.0, total_value - base_value)
    return total_value


def _apply_rules(
    stats: dict[str, float],
    *,
    rules: list[dict[str, Any]],
    total_attributes: dict[str, float],
    own_attributes: dict[str, float],
) -> None:
    for rule in rules:
        source_value = _source_value_for_rule(
            rule=rule,
            total_attributes=total_attributes,
            own_attributes=own_attributes,
        )
        if not source_value:
            continue
        stats[rule["target"]] = float(stats.get(rule["target"], 0.0) or 0.0) + (
            source_value * float(rule["multiplier"])
        )


def _compute_rule_total(
    *,
    target: str,
    rules: list[dict[str, Any]],
    total_attributes: dict[str, float],
    own_attributes: dict[str, float],
) -> int:
    value = 0.0
    for rule in rules:
        if rule["target"] != target:
            continue
        value += _source_value_for_rule(
            rule=rule,
            total_attributes=total_attributes,
            own_attributes=own_attributes,
        ) * float(rule["multiplier"])
    return max(0, int(math.ceil(value)))


def _bonus_lookup(source: Any, stat_key: str) -> float:
    if source is None:
        return 0.0
    attributes = getattr(source, "attributes", None)
    if isinstance(attributes, dict) and stat_key in attributes:
        raw_value = attributes.get(stat_key, 0)
        if _is_number(raw_value):
            return float(raw_value)
        return 0.0
    raw_value = getattr(source, stat_key, 0)
    if not _is_number(raw_value):
        return 0.0
    return float(raw_value)


def _attribute_values(source: Any, allowed_keys: list[str]) -> dict[str, float]:
    if source is None:
        return {}
    raw_values = getattr(source, "attributes", None)
    if not isinstance(raw_values, dict):
        return {}
    values: dict[str, float] = {}
    allowed = set(allowed_keys)
    for raw_key, raw_value in raw_values.items():
        key = str(raw_key or "").strip()
        if key not in allowed or not _is_number(raw_value):
            continue
        values[key] = float(raw_value)
    return values


def _normalize_attribute_values(values: Any, allowed_keys: list[str]) -> dict[str, float]:
    if not isinstance(values, dict):
        return {}
    normalized: dict[str, float] = {}
    allowed = set(allowed_keys)
    for raw_key, raw_value in values.items():
        key = str(raw_key or "").strip()
        if key not in allowed or not _is_number(raw_value):
            continue
        normalized[key] = normalized.get(key, 0.0) + float(raw_value)
    return normalized


def compute_attribute_formula_stats(
    *,
    world: Any,
    attributes: dict[str, Any],
    archetype: str | None = None,
) -> dict[str, int]:
    """
    Compute stat bonuses produced by explicit attributes only.

    This is for authored mobs that persist direct stat columns. A mob definition
    can say both `attack_power: 3` and `attributes: {strength: 5}`; the direct
    stat remains explicit, and this helper returns the formula-derived bonus
    that should be added before saving the concrete mob.
    """
    stat_system = get_world_stat_system(world)
    attribute_keys = get_attribute_order(stat_system)
    own_attributes = _normalize_attribute_values(attributes, attribute_keys)

    stats: dict[str, float] = {key: 0.0 for key in CANONICAL_COMPUTED_STAT_KEYS}
    formulas = stat_system["formulas"]

    base_resources = formulas["base_resources"]
    for resource_key, target_key in (
        ("health", "health_max"),
        ("energy", "energy_max"),
        ("stamina", "stamina_max"),
    ):
        resource_spec = base_resources.get(resource_key, {}) or {}
        if not resource_spec.get("source"):
            continue
        value = _evaluate_base_resource(
            resource_spec,
            own_attributes=own_attributes,
        )
        stats[target_key] += value
        if resource_key == "energy":
            stats["energy_base"] += value
        elif resource_key == "stamina":
            stats["stamina_base"] += value
        elif resource_key == "health":
            stats["health_base"] += value

    profile = stat_system["class_profiles"].get(archetype or "")
    if profile is None:
        profile = stat_system["default_profile"]

    _apply_rules(
        stats,
        rules=formulas["global_rules"],
        total_attributes=own_attributes,
        own_attributes=own_attributes,
    )
    _apply_rules(
        stats,
        rules=profile["derived_rules"],
        total_attributes=own_attributes,
        own_attributes=own_attributes,
    )

    return {
        key: max(0, int(math.ceil(float(stats.get(key) or 0.0))))
        for key in DIRECT_STAT_KEYS
    }


def fold_declared_attributes(
    attrs: dict[str, Any],
    *,
    world: Any,
    candidate_keys: Iterable[str],
) -> None:
    """
    Move loose generated stat keys into attributes only when the world
    declares those keys. Unknown keys are dropped silently.
    """
    stat_system = get_world_stat_system(world)
    declared_keys = set(get_attribute_order(stat_system))
    if not declared_keys:
        attrs.pop("attributes", None)
        for key in candidate_keys:
            attrs.pop(key, None)
        return

    normalized: dict[str, float] = {}
    raw_attributes = attrs.pop("attributes", {}) or {}
    if isinstance(raw_attributes, dict):
        for raw_key, raw_value in raw_attributes.items():
            key = str(raw_key or "").strip()
            if key in declared_keys and _is_number(raw_value):
                normalized[key] = normalized.get(key, 0.0) + float(raw_value)

    for raw_key in candidate_keys:
        key = str(raw_key or "").strip()
        raw_value = attrs.pop(key, 0)
        if key in declared_keys and _is_number(raw_value) and raw_value:
            normalized[key] = normalized.get(key, 0.0) + float(raw_value)

    if normalized:
        attrs["attributes"] = normalized


def _iter_equipment_items(char: Any) -> list[Any]:
    equipment = getattr(char, "equipment", None)
    if not equipment:
        return []
    try:
        return [item for item in equipment if item]
    except TypeError:
        items = []
        for slot in constants.EQUIPMENT_SLOTS:
            item = getattr(equipment, slot, None)
            if item:
                items.append(item)
        return items


def _evaluate_base_resource(
    spec: dict[str, Any],
    *,
    own_attributes: dict[str, float],
) -> float:
    if not spec:
        return 0.0
    if "flat" in spec:
        return float(spec["flat"])
    source = spec.get("source")
    multiplier = float(spec.get("multiplier") or 0.0)
    return float(own_attributes.get(source, 0.0) or 0.0) * multiplier


def _derive_runtime_world(char: Any = None, world=None):
    if world is not None:
        return world
    if char is None:
        return None
    return getattr(char, "world", None)


def compute_stats(
    level,
    archetype=None,
    char=None,
    boost_mob=False,
    is_mob=False,
    faction_level=0,
    world=None,
):
    """
    Compute derived stats for a character against the world-authored stat
    system.

    The returned payload uses WR2 stat names only.
    """

    runtime_world = _derive_runtime_world(char=char, world=world)
    stat_system = get_world_stat_system(runtime_world)
    attribute_keys = get_attribute_order(stat_system)

    stats: dict[str, float] = {}
    for key in attribute_keys:
        stats[key] = 0.0
    for key in CANONICAL_COMPUTED_STAT_KEYS:
        stats[key] = 0.0

    profile = stat_system["class_profiles"].get(archetype or "")
    if profile is None:
        profile = stat_system["default_profile"]

    weights = profile["attribute_weights"]
    for key in attribute_keys:
        stats[key] = math.ceil(config.ILF(level) * float(weights.get(key, 0.0) or 0.0))

    if faction_level:
        faction_multiplier = 1 + (float(faction_level) * config.FACTION_STAT_BONUS / 100)
        for key in attribute_keys:
            stats[key] = math.ceil(float(stats.get(key, 0.0) or 0.0) * faction_multiplier)

    for key, value in _attribute_values(char, attribute_keys).items():
        stats[key] += value

    own_attributes = {key: float(stats.get(key, 0.0) or 0.0) for key in attribute_keys}

    formulas = stat_system["formulas"]
    base_resources = formulas["base_resources"]
    stats["energy_base"] = _evaluate_base_resource(
        base_resources.get("energy", {}),
        own_attributes=own_attributes,
    )
    stats["stamina_base"] = _evaluate_base_resource(
        base_resources.get("stamina", {}),
        own_attributes=own_attributes,
    )
    stats["health_max"] += _evaluate_base_resource(
        base_resources.get("health", {}),
        own_attributes=own_attributes,
    )
    stats["energy_max"] += stats["energy_base"]
    stats["stamina_max"] += stats["stamina_base"]

    additive_stat_keys = set(attribute_keys)
    additive_stat_keys.update(
        (
            "health_max",
            "health_regen",
            "energy_max",
            "energy_regen",
            "stamina_max",
            "stamina_regen",
            "attack_power",
            "ability_power",
            "armor",
            "crit",
            "dodge",
            "resilience",
        )
    )

    if char is not None:
        for item in _iter_equipment_items(char):
            augment = getattr(item, "augment", None)
            for stat_key in additive_stat_keys:
                stats[stat_key] += _bonus_lookup(item, stat_key)
                if augment is not None:
                    stats[stat_key] += _bonus_lookup(augment, stat_key)

    if boost_mob:
        mob_boost = formulas["mob_boost"]
        stats_boost = math.ceil(config.ILF(level) * float(mob_boost["slot_factor"]))
        if boost_mob == "elite":
            stats_boost = math.ceil(stats_boost * float(mob_boost["elite_multiplier"]))

        remaining_stats = max(0, stats_boost)
        main_target = profile.get("main_attribute") or ""
        if main_target and main_target in attribute_keys:
            stats[main_target] += remaining_stats
        else:
            fallback_targets = list(attribute_keys)
            if fallback_targets:
                share = remaining_stats // len(fallback_targets)
                extra = remaining_stats % len(fallback_targets)
                for index, key in enumerate(fallback_targets):
                    stats[key] += share + (1 if index < extra else 0)

        stats["armor"] = math.ceil(10.25 * config.ILF(level))
        armor_map = mob_boost["armor_multiplier_by_profile"]
        armor_multiplier = float(
            armor_map.get(archetype or "", armor_map.get("default", 1))
        )
        stats["armor"] = math.ceil(stats["armor"] * armor_multiplier)

    total_attributes = {key: float(stats.get(key, 0.0) or 0.0) for key in attribute_keys}
    global_rules = formulas["global_rules"]
    _apply_rules(
        stats,
        rules=global_rules,
        total_attributes=total_attributes,
        own_attributes=own_attributes,
    )
    _apply_rules(
        stats,
        rules=profile["derived_rules"],
        total_attributes=total_attributes,
        own_attributes=own_attributes,
    )

    equipped_weapon = None
    if char is not None:
        equipment = getattr(char, "equipment", None)
        equipped_weapon = getattr(equipment, "weapon", None) if equipment else None

    if boost_mob or (
        equipped_weapon is not None
        and getattr(equipped_weapon, "equipment_type", None) == constants.EQUIPMENT_TYPE_WEAPON_2H
    ):
        for stat_key, multiplier in formulas["two_handed_multipliers"].items():
            stats[stat_key] = math.ceil(float(stats.get(stat_key, 0.0) or 0.0) * float(multiplier))

    stats["health_base"] = _compute_rule_total(
        target="health_max",
        rules=global_rules + profile["derived_rules"],
        total_attributes=own_attributes,
        own_attributes=own_attributes,
    )

    finalized: dict[str, int] = {}
    for key, value in stats.items():
        finalized[key] = max(0, int(math.ceil(float(value or 0.0))))

    for key in attribute_keys:
        finalized.setdefault(key, 0)
    for key in (
        "health_max",
        "health_regen",
        "stamina_base",
        "stamina_max",
        "stamina_regen",
        "attack_power",
        "ability_power",
        "armor",
        "crit",
        "dodge",
        "resilience",
        "energy_base",
        "energy_max",
        "energy_regen",
    ):
        finalized.setdefault(key, 0)

    return finalized


def build_player_stat_payload(player) -> dict[str, Any]:
    runtime_world = getattr(player, "world", None)
    stat_system = get_world_stat_system(runtime_world)
    stats = compute_stats(
        player.level,
        player.archetype,
        char=player,
        world=runtime_world,
    )
    attribute_order = get_attribute_order(stat_system)
    derived_order = list(stat_system["derived_display_order"])
    health_max = max(
        int(stats.get("health_max") or 0),
        int(getattr(player, "health", 0) or 0),
    )
    energy_max = max(
        int(stats.get("energy_max") or 0),
        int(getattr(player, "energy", 0) or 0),
    )
    stamina_max = max(
        int(stats.get("stamina_max") or 0),
        int(getattr(player, "stamina", 0) or 0),
    )
    return {
        "attributes": {
            key: int(stats.get(key) or 0)
            for key in attribute_order
        },
        "derived_stats": {
            key: int(stats.get(key) or 0)
            for key in derived_order
        },
        "energy": int(getattr(player, "energy", 0) or 0),
        "energy_base": int(stats.get("energy_base") or 0),
        "energy_max": energy_max,
        "energy_regen": int(stats.get("energy_regen") or 0),
        "ability_power": int(stats.get("ability_power") or 0),
        "health_base": int(stats.get("health_base") or 0),
        "health_max": health_max,
        "health_regen": int(stats.get("health_regen") or 0),
        "stamina_base": int(stats.get("stamina_base") or 0),
        "stamina_max": stamina_max,
        "stamina_regen": int(stats.get("stamina_regen") or 0),
        "attack_power": int(stats.get("attack_power") or 0),
        "armor": int(stats.get("armor") or 0),
        "crit": int(stats.get("crit") or 0),
        "dodge": int(stats.get("dodge") or 0),
        "resilience": int(stats.get("resilience") or 0),
    }
