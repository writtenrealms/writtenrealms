"""
World-authored stat system support for WR2.

This module keeps the engine contract small and stable while allowing worlds
to customize:

- primary attribute labels and ordering
- class/archetype attribute weight profiles
- formula coefficients that map primaries into canonical combat stats
- player-facing labels for resources and derived stats

Canonical runtime names intentionally use WR2 terminology:

- energy / energy_max / energy_regen
- ability_power

Legacy field aliases remain available because the current database schema and
many serializers still use WR1-oriented names such as mana and spell_power.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from config import constants
from config import game_settings as config


class StatSystemValidationError(ValueError):
    pass


PRIMARY_ATTRIBUTE_FALLBACK_ORDER = [
    constants.ATTR_CON,
    constants.ATTR_STR,
    constants.ATTR_DEX,
    constants.ATTR_INT,
]

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

CANONICAL_TO_LEGACY = {
    "energy": "mana",
    "energy_base": "mana_base",
    "energy_max": "mana_max",
    "energy_regen": "mana_regen",
    "ability_power": "spell_power",
}
LEGACY_TO_CANONICAL = {
    legacy: canonical for canonical, legacy in CANONICAL_TO_LEGACY.items()
}

DEFAULT_STAT_SYSTEM = {
    "primary_attributes": [
        {"key": constants.ATTR_CON, "label": "Constitution"},
        {"key": constants.ATTR_STR, "label": "Strength"},
        {"key": constants.ATTR_DEX, "label": "Dexterity"},
        {"key": constants.ATTR_INT, "label": "Intelligence"},
    ],
    "labels": {
        "resources": {
            "health": "Health",
            "energy": "Mana",
            "stamina": "Stamina",
        },
        "derived": {
            "attack_power": "Attack Power",
            "ability_power": "Spell Power",
            "armor": "Armor",
            "crit": "Crit",
            "dodge": "Dodge",
            "resilience": "Resilience",
            "health_regen": "Health Regen",
            "energy_regen": "Mana Regen",
            "stamina_regen": "Stamina Regen",
        },
        "classes": {
            "": "Classless",
            constants.ARCHETYPE_WARRIOR: "Warrior",
            constants.ARCHETYPE_ASSASSIN: "Assassin",
            constants.ARCHETYPE_MAGE: "Mage",
            constants.ARCHETYPE_CLERIC: "Cleric",
        },
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
        "label": "Classless",
        "primary_attribute": "",
        "base_attribute_weights": {
            constants.ATTR_CON: 3,
            constants.ATTR_STR: 2,
            constants.ATTR_DEX: 2,
            constants.ATTR_INT: 2,
        },
        "derived_rules": [],
    },
    "class_profiles": {
        constants.ARCHETYPE_WARRIOR: {
            "label": "Warrior",
            "primary_attribute": constants.ATTR_STR,
            "base_attribute_weights": {
                constants.ATTR_CON: 3,
                constants.ATTR_STR: 4,
                constants.ATTR_DEX: 1,
                constants.ATTR_INT: 1,
            },
            "derived_rules": [
                {
                    "source": constants.ATTR_STR,
                    "target": "crit",
                    "multiplier": 1,
                    "mode": "total",
                }
            ],
        },
        constants.ARCHETYPE_ASSASSIN: {
            "label": "Assassin",
            "primary_attribute": constants.ATTR_DEX,
            "base_attribute_weights": {
                constants.ATTR_CON: 3,
                constants.ATTR_STR: 1,
                constants.ATTR_DEX: 4,
                constants.ATTR_INT: 1,
            },
            "derived_rules": [
                {
                    "source": constants.ATTR_DEX,
                    "target": "attack_power",
                    "multiplier": 1,
                    "mode": "total",
                }
            ],
        },
        constants.ARCHETYPE_MAGE: {
            "label": "Mage",
            "primary_attribute": constants.ATTR_INT,
            "base_attribute_weights": {
                constants.ATTR_CON: 3,
                constants.ATTR_STR: 1,
                constants.ATTR_DEX: 1,
                constants.ATTR_INT: 4,
            },
            "derived_rules": [],
        },
        constants.ARCHETYPE_CLERIC: {
            "label": "Cleric",
            "primary_attribute": constants.ATTR_INT,
            "base_attribute_weights": {
                constants.ATTR_CON: 3,
                constants.ATTR_STR: 1,
                constants.ATTR_DEX: 1,
                constants.ATTR_INT: 4,
            },
            "derived_rules": [],
        },
    },
    "formulas": {
        "base_resources": {
            "energy": {
                "source": constants.ATTR_INT,
                "multiplier": 2,
            },
            "stamina": {
                "flat": config.PLAYER_STARTING_MAX_STAMINA,
            },
        },
        "global_rules": [
            {
                "source": constants.ATTR_CON,
                "target": "health_max",
                "multiplier": 2,
                "mode": "total",
            },
            {
                "source": constants.ATTR_CON,
                "target": "resilience",
                "multiplier": 1,
                "mode": "total",
            },
            {
                "source": constants.ATTR_STR,
                "target": "attack_power",
                "multiplier": 1,
                "mode": "total",
            },
            {
                "source": constants.ATTR_STR,
                "target": "health_max",
                "multiplier": 1,
                "mode": "total",
            },
            {
                "source": constants.ATTR_INT,
                "target": "ability_power",
                "multiplier": 2,
                "mode": "total",
            },
            {
                "source": constants.ATTR_INT,
                "target": "energy_max",
                "multiplier": 1,
                "mode": "bonus_from_total_minus_base",
            },
            {
                "source": constants.ATTR_DEX,
                "target": "dodge",
                "multiplier": 1,
                "mode": "total",
            },
            {
                "source": constants.ATTR_DEX,
                "target": "crit",
                "multiplier": 1,
                "mode": "total",
            },
        ],
        "two_handed_multipliers": {
            "attack_power": 1.5,
            "ability_power": 1.5,
        },
        "mob_boost": {
            "slot_factor": 10.25,
            "elite_multiplier": 1.2,
            "constitution_share": 0.5,
            "armor_multiplier_by_profile": {
                constants.ARCHETYPE_WARRIOR: 3,
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
    allow_empty_keys: bool = False,
) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise StatSystemValidationError(f"{field_name} must be a mapping.")

    normalized: dict[str, str] = {}
    for raw_key, raw_label in value.items():
        key = str(raw_key if raw_key is not None else "").strip()
        if not key and not allow_empty_keys:
            raise StatSystemValidationError(
                f"{field_name} keys must be non-empty strings."
            )
        if allowed_keys and key not in allowed_keys:
            raise StatSystemValidationError(
                f"{field_name}.{key} is not supported."
            )
        fallback_label = key.replace("_", " ").title()
        if allow_empty_keys and not key:
            fallback_label = "Classless"
        normalized[key] = str(raw_label or "").strip() or fallback_label
    return normalized


def _coerce_primary_attributes(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return deepcopy(DEFAULT_STAT_SYSTEM["primary_attributes"])
    if not isinstance(value, list) or not value:
        raise StatSystemValidationError(
            "stats.primary_attributes must be a non-empty list."
        )

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise StatSystemValidationError(
                f"stats.primary_attributes[{index}] must be a mapping."
            )
        key = str(entry.get("key") or "").strip()
        if not key:
            raise StatSystemValidationError(
                f"stats.primary_attributes[{index}].key is required."
            )
        if key in seen:
            raise StatSystemValidationError(
                f"stats.primary_attributes contains duplicate key '{key}'."
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
            f"{field_name}.source must reference a declared primary attribute."
        )
    if target not in CANONICAL_COMPUTED_STAT_KEYS:
        raise StatSystemValidationError(
            f"{field_name}.target must be a canonical computed stat."
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
    primary_keys: list[str],
) -> dict[str, float]:
    if value in (None, ""):
        return {key: 0.0 for key in primary_keys}
    if not isinstance(value, dict):
        raise StatSystemValidationError(f"{field_name} must be a mapping.")

    normalized: dict[str, float] = {key: 0.0 for key in primary_keys}
    for key, raw_value in value.items():
        stat_key = str(key or "").strip()
        if stat_key not in normalized:
            raise StatSystemValidationError(
                f"{field_name}.{stat_key} must reference a declared primary attribute."
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
    primary_keys: list[str],
    default_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if raw_profile in (None, ""):
        raw_profile = {}
    if not isinstance(raw_profile, dict):
        raise StatSystemValidationError(f"{field_name} must be a mapping.")

    base_profile = deepcopy(default_profile or {})

    label = str(raw_profile.get("label") or base_profile.get("label") or "").strip()
    primary_attribute = str(
        raw_profile.get("primary_attribute")
        if "primary_attribute" in raw_profile
        else base_profile.get("primary_attribute") or ""
    ).strip()
    if primary_attribute and primary_attribute not in primary_keys:
        raise StatSystemValidationError(
            f"{field_name}.primary_attribute must reference a declared primary attribute."
        )

    weights = _coerce_weights(
        raw_profile.get("base_attribute_weights", base_profile.get("base_attribute_weights")),
        field_name=f"{field_name}.base_attribute_weights",
        primary_keys=primary_keys,
    )
    rules = _coerce_rule_list(
        raw_profile.get("derived_rules", base_profile.get("derived_rules")),
        field_name=f"{field_name}.derived_rules",
        allowed_sources=set(primary_keys),
    )

    return {
        "label": label,
        "primary_attribute": primary_attribute,
        "base_attribute_weights": weights,
        "derived_rules": rules,
    }


def _coerce_base_resource_config(
    value: Any,
    *,
    field_name: str,
    primary_keys: list[str],
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
        if source not in primary_keys:
            raise StatSystemValidationError(
                f"{field_name}.source must reference a declared primary attribute."
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
    if value in (None, "", {}):
        return deepcopy(DEFAULT_STAT_SYSTEM)
    if not isinstance(value, dict):
        raise StatSystemValidationError("stats must be a mapping.")

    normalized = deepcopy(DEFAULT_STAT_SYSTEM)

    primary_attributes = _coerce_primary_attributes(value.get("primary_attributes"))
    primary_keys = [entry["key"] for entry in primary_attributes]
    normalized["primary_attributes"] = primary_attributes

    raw_labels = value.get("labels") or {}
    if raw_labels not in ({}, None) and not isinstance(raw_labels, dict):
        raise StatSystemValidationError("stats.labels must be a mapping.")
    raw_class_labels = _coerce_label_map(
        (raw_labels or {}).get("classes"),
        field_name="stats.labels.classes",
        allowed_keys=None,
        allow_empty_keys=True,
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
        primary_keys=primary_keys,
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
            primary_keys=primary_keys,
            default_profile=class_profiles.get(normalized_key),
        )
        if class_profiles[normalized_key]["label"]:
            normalized["labels"]["classes"][normalized_key] = class_profiles[normalized_key]["label"]
    normalized["class_profiles"] = class_profiles
    if normalized["default_profile"]["label"]:
        normalized["labels"]["classes"][""] = normalized["default_profile"]["label"]

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
            primary_keys=primary_keys,
            default_value=base_resources.get(resource_key),
        )
    formulas["base_resources"] = base_resources

    formulas["global_rules"] = _coerce_rule_list(
        raw_formulas.get("global_rules", formulas.get("global_rules")),
        field_name="stats.formulas.global_rules",
        allowed_sources=set(primary_keys),
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
        for key in ("slot_factor", "elite_multiplier", "constitution_share"):
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


def get_primary_attribute_order(stat_system: dict[str, Any]) -> list[str]:
    return [entry["key"] for entry in stat_system["primary_attributes"]]


def get_world_label_bundle(world) -> dict[str, Any]:
    stat_system = get_world_stat_system(world)
    return {
        "resources": deepcopy(stat_system["labels"]["resources"]),
        "primaries": {
            entry["key"]: entry["label"]
            for entry in stat_system["primary_attributes"]
        },
        "derived": deepcopy(stat_system["labels"]["derived"]),
        "classes": deepcopy(stat_system["labels"]["classes"]),
        "order": {
            "resources": list(CANONICAL_RESOURCE_LABEL_KEYS),
            "primaries": get_primary_attribute_order(stat_system),
            "derived": list(stat_system["derived_display_order"]),
        },
    }


def _source_value_for_rule(
    *,
    rule: dict[str, Any],
    total_primaries: dict[str, float],
    base_primaries: dict[str, float],
) -> float:
    source = rule["source"]
    mode = rule["mode"]
    total_value = float(total_primaries.get(source, 0.0) or 0.0)
    base_value = float(base_primaries.get(source, 0.0) or 0.0)
    if mode == "base_only":
        return base_value
    if mode == "bonus_from_total_minus_base":
        return max(0.0, total_value - base_value)
    return total_value


def _apply_rules(
    stats: dict[str, float],
    *,
    rules: list[dict[str, Any]],
    total_primaries: dict[str, float],
    base_primaries: dict[str, float],
) -> None:
    for rule in rules:
        source_value = _source_value_for_rule(
            rule=rule,
            total_primaries=total_primaries,
            base_primaries=base_primaries,
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
    total_primaries: dict[str, float],
    base_primaries: dict[str, float],
) -> int:
    value = 0.0
    for rule in rules:
        if rule["target"] != target:
            continue
        value += _source_value_for_rule(
            rule=rule,
            total_primaries=total_primaries,
            base_primaries=base_primaries,
        ) * float(rule["multiplier"])
    return max(0, int(math.ceil(value)))


def _bonus_lookup(source: Any, stat_key: str) -> float:
    if source is None:
        return 0.0
    lookup_key = CANONICAL_TO_LEGACY.get(stat_key, stat_key)
    raw_value = getattr(source, lookup_key, 0)
    if not _is_number(raw_value):
        return 0.0
    return float(raw_value)


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
    base_primaries: dict[str, float],
) -> float:
    if not spec:
        return 0.0
    if "flat" in spec:
        return float(spec["flat"])
    source = spec.get("source")
    multiplier = float(spec.get("multiplier") or 0.0)
    return float(base_primaries.get(source, 0.0) or 0.0) * multiplier


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

    The returned payload includes both canonical WR2 keys and legacy WR1-style
    aliases so the rest of the codebase can migrate incrementally.
    """

    runtime_world = _derive_runtime_world(char=char, world=world)
    stat_system = get_world_stat_system(runtime_world)
    primary_keys = get_primary_attribute_order(stat_system)

    stats: dict[str, float] = {}
    for key in set(primary_keys + PRIMARY_ATTRIBUTE_FALLBACK_ORDER):
        stats[key] = 0.0
    for key in CANONICAL_COMPUTED_STAT_KEYS:
        stats[key] = 0.0

    profile = stat_system["class_profiles"].get(archetype or "")
    if profile is None:
        profile = stat_system["default_profile"]

    weights = profile["base_attribute_weights"]
    for key in primary_keys:
        stats[key] = math.ceil(config.ILF(level) * float(weights.get(key, 0.0) or 0.0))

    base_primaries = {key: float(stats.get(key, 0.0) or 0.0) for key in primary_keys}

    if faction_level:
        faction_multiplier = 1 + (float(faction_level) * config.FACTION_STAT_BONUS / 100)
        for key in primary_keys:
            stats[key] = math.ceil(float(stats.get(key, 0.0) or 0.0) * faction_multiplier)

    formulas = stat_system["formulas"]
    base_resources = formulas["base_resources"]
    stats["energy_base"] = _evaluate_base_resource(
        base_resources.get("energy", {}),
        base_primaries=base_primaries,
    )
    stats["stamina_base"] = _evaluate_base_resource(
        base_resources.get("stamina", {}),
        base_primaries=base_primaries,
    )
    stats["health_max"] += _evaluate_base_resource(
        base_resources.get("health", {}),
        base_primaries=base_primaries,
    )
    stats["energy_max"] += stats["energy_base"]
    stats["stamina_max"] += stats["stamina_base"]

    additive_stat_keys = set(primary_keys)
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

        constitution_share = float(mob_boost["constitution_share"])
        con_stats = int(round(stats_boost * constitution_share))
        remaining_stats = max(0, stats_boost - con_stats)
        if constants.ATTR_CON in primary_keys:
            stats[constants.ATTR_CON] += con_stats
        else:
            remaining_stats += con_stats

        primary_target = profile.get("primary_attribute") or ""
        if primary_target and primary_target in primary_keys:
            stats[primary_target] += remaining_stats
        else:
            fallback_targets = [key for key in primary_keys if key != constants.ATTR_CON]
            if not fallback_targets:
                fallback_targets = list(primary_keys)
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

    total_primaries = {key: float(stats.get(key, 0.0) or 0.0) for key in primary_keys}
    global_rules = formulas["global_rules"]
    _apply_rules(
        stats,
        rules=global_rules,
        total_primaries=total_primaries,
        base_primaries=base_primaries,
    )
    _apply_rules(
        stats,
        rules=profile["derived_rules"],
        total_primaries=total_primaries,
        base_primaries=base_primaries,
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
        total_primaries=base_primaries,
        base_primaries=base_primaries,
    )

    finalized: dict[str, int] = {}
    for key, value in stats.items():
        finalized[key] = max(0, int(math.ceil(float(value or 0.0))))

    finalized["mana_base"] = finalized["energy_base"]
    finalized["mana_max"] = finalized["energy_max"]
    finalized["mana_regen"] = finalized["energy_regen"]
    finalized["spell_power"] = finalized["ability_power"]

    for key in primary_keys:
        finalized.setdefault(key, 0)
    for key in PRIMARY_ATTRIBUTE_FALLBACK_ORDER:
        finalized.setdefault(key, 0)
    for key in (
        "health_max",
        "health_regen",
        "stamina_base",
        "stamina_max",
        "stamina_regen",
        "attack_power",
        "ability_power",
        "spell_power",
        "armor",
        "crit",
        "dodge",
        "resilience",
        "energy_base",
        "energy_max",
        "energy_regen",
        "mana_base",
        "mana_max",
        "mana_regen",
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
    primary_order = get_primary_attribute_order(stat_system)
    derived_order = list(stat_system["derived_display_order"])
    health_max = max(
        int(stats.get("health_max") or 0),
        int(getattr(player, "health", 0) or 0),
    )
    energy_max = max(
        int(stats.get("energy_max") or 0),
        int(getattr(player, "mana", 0) or 0),
    )
    stamina_max = max(
        int(stats.get("stamina_max") or 0),
        int(getattr(player, "stamina", 0) or 0),
    )
    return {
        "primary_attributes": {
            key: int(stats.get(key) or 0)
            for key in primary_order
        },
        "derived_stats": {
            key: int(stats.get(key) or 0)
            for key in derived_order
        },
        "energy": int(getattr(player, "mana", 0) or 0),
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
        "mana_base": int(stats.get("mana_base") or 0),
        "mana_max": energy_max,
        "mana_regen": int(stats.get("mana_regen") or 0),
        "spell_power": int(stats.get("spell_power") or 0),
        "attack_power": int(stats.get("attack_power") or 0),
        "armor": int(stats.get("armor") or 0),
        "crit": int(stats.get("crit") or 0),
        "dodge": int(stats.get("dodge") or 0),
        "resilience": int(stats.get("resilience") or 0),
        "strength": int(stats.get(constants.ATTR_STR) or 0),
        "constitution": int(stats.get(constants.ATTR_CON) or 0),
        "dexterity": int(stats.get(constants.ATTR_DEX) or 0),
        "intelligence": int(stats.get(constants.ATTR_INT) or 0),
    }
