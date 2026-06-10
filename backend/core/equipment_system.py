from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from config import constants


class EquipmentSystemValidationError(ValueError):
    pass


DEFAULT_ARMOR_SUGGESTION_SLOT_WEIGHTS = {
    constants.EQUIPMENT_TYPE_HEAD: 0.15,
    constants.EQUIPMENT_TYPE_BODY: 0.30,
    constants.EQUIPMENT_TYPE_ARMS: 0.10,
    constants.EQUIPMENT_TYPE_HANDS: 0.10,
    constants.EQUIPMENT_TYPE_WAIST: 0.10,
    constants.EQUIPMENT_TYPE_LEGS: 0.15,
    constants.EQUIPMENT_TYPE_FEET: 0.10,
    constants.EQUIPMENT_TYPE_SHIELD: 0.35,
}

DEFAULT_EQUIPMENT_SYSTEM = {
    "armor_classes": [],
    "default_armor_class": "",
    "armor_suggestions": {
        "full_set_scale": 0.35,
        "slot_weights": DEFAULT_ARMOR_SUGGESTION_SLOT_WEIGHTS,
    },
}

ARMOR_PROFICIENCY_EQUIPMENT_TYPES = (
    *constants.EQUIPMENT_ARMOR,
    constants.EQUIPMENT_TYPE_SHIELD,
)


@dataclass(frozen=True)
class EquipmentPolicyResult:
    allowed: bool
    message: str = ""
    code: str = ""


def default_equipment_system() -> dict[str, Any]:
    return deepcopy(DEFAULT_EQUIPMENT_SYSTEM)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coerce_armor_classes(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise EquipmentSystemValidationError("equipment.armor_classes must be a list.")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise EquipmentSystemValidationError(
                f"equipment.armor_classes[{index}] must be a mapping."
            )
        key = str(entry.get("key") or "").strip()
        if not key:
            raise EquipmentSystemValidationError(
                f"equipment.armor_classes[{index}].key is required."
            )
        if key in seen:
            raise EquipmentSystemValidationError(
                f"equipment.armor_classes contains duplicate key '{key}'."
            )
        seen.add(key)

        armor_multiplier = entry.get("armor_multiplier", 1.0)
        if not _is_number(armor_multiplier) or float(armor_multiplier) < 0:
            raise EquipmentSystemValidationError(
                f"equipment.armor_classes[{index}].armor_multiplier must be a number >= 0."
            )

        normalized.append(
            {
                "key": key,
                "label": str(entry.get("label") or "").strip()
                or key.replace("_", " ").title(),
                "description": str(entry.get("description") or "").strip(),
                "armor_multiplier": float(armor_multiplier),
            }
        )
    return normalized


def _coerce_armor_suggestions(value: Any) -> dict[str, Any]:
    normalized = deepcopy(DEFAULT_EQUIPMENT_SYSTEM["armor_suggestions"])
    if value in (None, ""):
        return normalized
    if not isinstance(value, dict):
        raise EquipmentSystemValidationError("equipment.armor_suggestions must be a mapping.")

    if "full_set_scale" in value:
        full_set_scale = value.get("full_set_scale")
        if not _is_number(full_set_scale) or float(full_set_scale) < 0:
            raise EquipmentSystemValidationError(
                "equipment.armor_suggestions.full_set_scale must be a number >= 0."
            )
        normalized["full_set_scale"] = float(full_set_scale)

    raw_slot_weights = value.get("slot_weights")
    if raw_slot_weights is not None:
        if not isinstance(raw_slot_weights, dict):
            raise EquipmentSystemValidationError(
                "equipment.armor_suggestions.slot_weights must be a mapping."
            )
        slot_weights = dict(normalized["slot_weights"])
        allowed_slots = set(DEFAULT_ARMOR_SUGGESTION_SLOT_WEIGHTS)
        for raw_slot, raw_weight in raw_slot_weights.items():
            slot = str(raw_slot or "").strip()
            if slot not in allowed_slots:
                raise EquipmentSystemValidationError(
                    f"equipment.armor_suggestions.slot_weights.{slot} is not supported."
                )
            if not _is_number(raw_weight) or float(raw_weight) < 0:
                raise EquipmentSystemValidationError(
                    f"equipment.armor_suggestions.slot_weights.{slot} must be a number >= 0."
                )
            slot_weights[slot] = float(raw_weight)
        normalized["slot_weights"] = slot_weights

    return normalized


def normalize_equipment_system(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise EquipmentSystemValidationError("equipment must be a mapping.")

    allowed_keys = {
        "armor_classes",
        "default_armor_class",
        "armor_suggestions",
    }
    unknown_keys = sorted(set(value.keys()) - allowed_keys)
    if unknown_keys:
        raise EquipmentSystemValidationError(
            f"Unsupported equipment field(s): {', '.join(unknown_keys)}."
        )

    normalized = default_equipment_system()
    normalized["armor_classes"] = _coerce_armor_classes(value.get("armor_classes"))
    armor_class_keys = get_armor_class_keys(normalized)

    default_armor_class = str(value.get("default_armor_class") or "").strip()
    if default_armor_class and default_armor_class not in armor_class_keys:
        raise EquipmentSystemValidationError(
            "equipment.default_armor_class must reference a declared armor class."
        )
    normalized["default_armor_class"] = default_armor_class
    normalized["armor_suggestions"] = _coerce_armor_suggestions(
        value.get("armor_suggestions")
    )
    return normalized


def get_armor_class_keys(equipment_system: dict[str, Any] | None) -> set[str]:
    if not isinstance(equipment_system, dict):
        return set()
    return {
        str(entry.get("key") or "").strip()
        for entry in equipment_system.get("armor_classes") or []
        if str(entry.get("key") or "").strip()
    }


def has_authored_armor_classes(equipment_system: dict[str, Any] | None) -> bool:
    return bool(get_armor_class_keys(equipment_system))


def get_world_equipment_system(world) -> dict[str, Any]:
    if world is None:
        return default_equipment_system()
    effective_config = getattr(world, "effective_config", None)
    config_obj = effective_config or getattr(world, "config", None)
    if config_obj is None:
        return default_equipment_system()
    return normalize_equipment_system(getattr(config_obj, "equipment_system", None))


def get_world_equipment_payload(world) -> dict[str, Any]:
    payload = get_world_equipment_system(world)
    from core.stat_system import get_world_stat_system

    stat_system = get_world_stat_system(world)
    default_profile = stat_system.get("default_profile") or {}
    class_profiles = stat_system.get("class_profiles") or {}
    proficiencies: dict[str, Any] = {
        "default": (
            list(default_profile["armor_proficiencies"])
            if "armor_proficiencies" in default_profile
            else None
        ),
        "classes": {},
    }
    for class_key, profile in class_profiles.items():
        if "armor_proficiencies" in profile:
            proficiencies["classes"][class_key] = list(profile["armor_proficiencies"])
    payload["armor_proficiencies"] = proficiencies
    return payload


def get_armor_class_label(
    equipment_system: dict[str, Any] | None,
    armor_class: str,
) -> str:
    key = str(armor_class or "").strip()
    for entry in (equipment_system or {}).get("armor_classes") or []:
        if entry.get("key") == key:
            return entry.get("label") or key.replace("_", " ").title()
    return key.replace("_", " ").title()


def validate_armor_class_reference(
    *,
    world,
    armor_class: Any,
    field_name: str,
) -> str:
    normalized_armor_class = str(armor_class or "").strip()
    if not normalized_armor_class:
        return ""

    equipment_system = get_world_equipment_system(world)
    armor_class_keys = get_armor_class_keys(equipment_system)
    if armor_class_keys and normalized_armor_class not in armor_class_keys:
        raise EquipmentSystemValidationError(
            f"{field_name} must reference a declared armor class."
        )
    return normalized_armor_class


def item_uses_armor_proficiency(item) -> bool:
    equipment_type = str(getattr(item, "equipment_type", "") or "").strip()
    return equipment_type in ARMOR_PROFICIENCY_EQUIPMENT_TYPES


def _legacy_heavy_armor_policy(actor, item) -> EquipmentPolicyResult:
    if (
        item_uses_armor_proficiency(item)
        and str(getattr(item, "armor_class", "") or "").strip() == constants.ARMOR_CLASS_HEAVY
        and getattr(actor, "archetype", None) != constants.ARCHETYPE_WARRIOR
    ):
        return EquipmentPolicyResult(
            allowed=False,
            message="You are not proficient with heavy armor.",
            code="armor_proficiency_required",
        )
    return EquipmentPolicyResult(allowed=True)


def can_actor_equip_item_by_armor_class(actor, item) -> EquipmentPolicyResult:
    armor_class = str(getattr(item, "armor_class", "") or "").strip()
    if not armor_class or not item_uses_armor_proficiency(item):
        return EquipmentPolicyResult(allowed=True)

    world = getattr(actor, "world", None)
    equipment_system = get_world_equipment_system(world)
    if not has_authored_armor_classes(equipment_system):
        return _legacy_heavy_armor_policy(actor, item)

    armor_class_keys = get_armor_class_keys(equipment_system)
    if armor_class not in armor_class_keys:
        label = get_armor_class_label(equipment_system, armor_class)
        return EquipmentPolicyResult(
            allowed=False,
            message=f"Unknown armor class: {label}.",
            code="unknown_armor_class",
        )

    from core.stat_system import get_world_stat_system

    stat_system = get_world_stat_system(world)
    class_profiles = stat_system.get("class_profiles") or {}
    profile = class_profiles.get(getattr(actor, "archetype", "") or "")
    if profile is None:
        profile = stat_system.get("default_profile") or {}

    if "armor_proficiencies" not in profile:
        return EquipmentPolicyResult(allowed=True)

    if armor_class in set(profile.get("armor_proficiencies") or []):
        return EquipmentPolicyResult(allowed=True)

    label = get_armor_class_label(equipment_system, armor_class).lower()
    return EquipmentPolicyResult(
        allowed=False,
        message=f"You are not proficient with {label}.",
        code="armor_proficiency_required",
    )
