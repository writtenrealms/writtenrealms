from __future__ import annotations

import copy
from typing import Any

from core.condition_dsl import validate_condition_payload


NUMERIC_MODIFIER_FIELDS = {
    "ability_power",
    "armor",
    "attack_power",
    "cost",
    "crit",
    "dodge",
    "energy_max",
    "energy_regen",
    "exp_worth",
    "food_value",
    "gold",
    "health_max",
    "health_regen",
    "level",
    "resilience",
    "stamina_max",
    "stamina_regen",
    "weapon_damage",
}

CURRENT_RESOURCE_FIELDS = {
    "energy_max": "energy",
    "health_max": "health",
    "stamina_max": "stamina",
}


def _non_empty_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required.")
    return text


def _mapping_or_empty(value: Any, *, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping.")
    return copy.deepcopy(value)


def normalize_trait_spec(value: Any, *, field_name: str = "trait", allow_weight: bool = False) -> dict[str, Any]:
    if isinstance(value, str):
        return {"key": _non_empty_text(value, field_name=field_name)}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a string or mapping.")

    normalized = copy.deepcopy(value)
    normalized["key"] = _non_empty_text(normalized.get("key"), field_name=f"{field_name}.key")

    if "params" in normalized:
        normalized["params"] = _mapping_or_empty(
            normalized.get("params"),
            field_name=f"{field_name}.params",
        )
    if "modifiers" in normalized:
        normalized["modifiers"] = _mapping_or_empty(
            normalized.get("modifiers"),
            field_name=f"{field_name}.modifiers",
        )
    if "runtime" in normalized:
        normalized["runtime"] = _mapping_or_empty(
            normalized.get("runtime"),
            field_name=f"{field_name}.runtime",
        )
    if "conditions" in normalized:
        conditions = copy.deepcopy(normalized.get("conditions") or {})
        validate_condition_payload(conditions, field_name=f"{field_name}.conditions")
        normalized["conditions"] = conditions
    if "visibility" in normalized:
        normalized["visibility"] = str(normalized.get("visibility") or "visible").strip() or "visible"
    if "version" in normalized:
        try:
            normalized["version"] = int(normalized.get("version") or 1)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name}.version must be an integer.")
    if allow_weight:
        try:
            normalized["weight"] = int(normalized.get("weight", 1) or 1)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name}.weight must be an integer.")
        if normalized["weight"] < 0:
            raise ValueError(f"{field_name}.weight cannot be negative.")
    return normalized


def normalize_trait_list(value: Any, *, field_name: str = "traits") -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list.")
    return [
        normalize_trait_spec(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(value)
    ]


def normalize_trait_table(value: Any, *, field_name: str = "traits") -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping.")

    normalized = copy.deepcopy(value)
    if "guaranteed" in normalized:
        guaranteed = normalized.get("guaranteed") or []
        if not isinstance(guaranteed, list):
            raise ValueError(f"{field_name}.guaranteed must be a list.")
        normalized["guaranteed"] = [
            normalize_trait_spec(item, field_name=f"{field_name}.guaranteed[{index}]")
            for index, item in enumerate(guaranteed)
        ]
    if "chance" in normalized:
        try:
            chance = int(normalized.get("chance") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name}.chance must be an integer.")
        if chance < 0 or chance > 100:
            raise ValueError(f"{field_name}.chance must be between 0 and 100.")
        normalized["chance"] = chance
    if "pool" in normalized:
        pool = normalized.get("pool") or []
        if not isinstance(pool, list):
            raise ValueError(f"{field_name}.pool must be a list.")
        normalized["pool"] = [
            normalize_trait_spec(
                item,
                field_name=f"{field_name}.pool[{index}]",
                allow_weight=True,
            )
            for index, item in enumerate(pool)
        ]
    return normalized


def trait_keys(traits: list[dict[str, Any]]) -> list[str]:
    keys = []
    for index, raw_trait in enumerate(traits or []):
        trait = normalize_trait_spec(raw_trait, field_name=f"traits[{index}]")
        key = str((trait or {}).get("key") or "").strip()
        if key:
            keys.append(key)
    return keys


def trait_modifiers(traits: list[dict[str, Any]]) -> dict[str, Any]:
    modifiers: dict[str, Any] = {}
    for index, raw_trait in enumerate(traits or []):
        trait = normalize_trait_spec(raw_trait, field_name=f"traits[{index}]")
        trait_mods = (trait or {}).get("modifiers")
        if isinstance(trait_mods, dict):
            modifiers.update(trait_mods)
    return modifiers


def _trait_instance_payload(
    trait: dict[str, Any],
    *,
    source: str,
    source_ref: str,
) -> dict[str, Any]:
    payload = {
        "key": str(trait.get("key") or "").strip(),
        "source": source,
        "source_ref": source_ref,
        "visibility": str(trait.get("visibility") or "visible").strip() or "visible",
        "params": copy.deepcopy(trait.get("params") or {}),
        "modifiers": copy.deepcopy(trait.get("modifiers") or {}),
        "runtime": copy.deepcopy(trait.get("runtime") or {}),
        "version": int(trait.get("version") or 1),
    }
    if trait.get("label") not in (None, ""):
        payload["label"] = str(trait.get("label") or "").strip()
    if trait.get("conditions") not in (None, {}, []):
        payload["conditions"] = copy.deepcopy(trait.get("conditions"))
    return payload


def trait_instances(
    traits: list[dict[str, Any]],
    *,
    source: str,
    source_ref: str,
) -> list[dict[str, Any]]:
    return [
        _trait_instance_payload(
            normalize_trait_spec(raw_trait, field_name=f"traits[{index}]"),
            source=source,
            source_ref=source_ref,
        )
        for index, raw_trait in enumerate(traits or [])
        if str(
            normalize_trait_spec(raw_trait, field_name=f"traits[{index}]").get("key") or ""
        ).strip()
    ]


def _numeric_modifier_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _minimum_for_numeric_field(field_name: str) -> int:
    if field_name in {"health_max", "level"}:
        return 1
    return 0


def apply_numeric_modifiers(entity: Any, modifiers: dict[str, Any]) -> list[str]:
    update_fields = []
    if not isinstance(modifiers, dict):
        return update_fields

    for raw_key, raw_value in modifiers.items():
        key = str(raw_key or "").strip()
        multiplier = False
        if key.endswith("_multiplier"):
            field_name = key[: -len("_multiplier")]
            multiplier = True
        else:
            field_name = key
        if field_name not in NUMERIC_MODIFIER_FIELDS or not hasattr(entity, field_name):
            continue
        modifier_value = _numeric_modifier_value(raw_value)
        current_value = _numeric_modifier_value(getattr(entity, field_name, None))
        if modifier_value is None or current_value is None:
            continue
        if multiplier:
            next_value = int(round(current_value * modifier_value))
        else:
            next_value = int(round(current_value + modifier_value))
        next_value = max(_minimum_for_numeric_field(field_name), next_value)
        setattr(entity, field_name, next_value)
        update_fields.append(field_name)
        resource_field = CURRENT_RESOURCE_FIELDS.get(field_name)
        if resource_field and hasattr(entity, resource_field):
            setattr(entity, resource_field, next_value)
            update_fields.append(resource_field)
    return list(dict.fromkeys(update_fields))


def modifiers_from_trait_instances(instances: list[dict[str, Any]]) -> dict[str, Any]:
    return trait_modifiers(instances or [])
