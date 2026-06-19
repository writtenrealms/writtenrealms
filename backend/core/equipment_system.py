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
    "offhand_weapons": {
        "default_allowed": False,
        "allowed_grips": [constants.WEAPON_GRIP_ONE_HAND],
    },
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


def _coerce_offhand_weapons(value: Any) -> dict[str, Any]:
    normalized = deepcopy(DEFAULT_EQUIPMENT_SYSTEM["offhand_weapons"])
    if value in (None, ""):
        return normalized
    if not isinstance(value, dict):
        raise EquipmentSystemValidationError("equipment.offhand_weapons must be a mapping.")

    unknown_fields = sorted(set(value.keys()) - {"default_allowed", "allowed_grips"})
    if unknown_fields:
        raise EquipmentSystemValidationError(
            "equipment.offhand_weapons has unsupported field(s): "
            + ", ".join(unknown_fields)
            + "."
        )
    if "default_allowed" in value:
        if not isinstance(value.get("default_allowed"), bool):
            raise EquipmentSystemValidationError(
                "equipment.offhand_weapons.default_allowed must be true or false."
            )
        normalized["default_allowed"] = value["default_allowed"]

    if "allowed_grips" in value:
        raw_grips = value.get("allowed_grips") or []
        if not isinstance(raw_grips, list):
            raise EquipmentSystemValidationError(
                "equipment.offhand_weapons.allowed_grips must be a list."
            )
        grips: list[str] = []
        for index, raw_grip in enumerate(raw_grips):
            grip = str(raw_grip or "").strip()
            if grip not in constants.WEAPON_GRIPS:
                raise EquipmentSystemValidationError(
                    f"equipment.offhand_weapons.allowed_grips[{index}] must be a supported weapon grip."
                )
            if grip not in grips:
                grips.append(grip)
        normalized["allowed_grips"] = grips
    return normalized


def normalize_equipment_system(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise EquipmentSystemValidationError("equipment must be a mapping.")

    allowed_keys = {
        "armor_classes",
        "default_armor_class",
        "offhand_weapons",
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
    normalized["offhand_weapons"] = _coerce_offhand_weapons(value.get("offhand_weapons"))
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


def has_authored_offhand_weapon_policy(equipment_system: dict[str, Any] | None) -> bool:
    return isinstance(equipment_system, dict) and "offhand_weapons" in equipment_system


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


def _item_equipment_type(item: Any) -> str:
    value = getattr(item, "equipment_type", None)
    if value is None and getattr(item, "template", None) is not None:
        value = getattr(item.template, "equipment_type", "")
    return str(value or "").strip()


def _item_weapon_grip(item: Any) -> str:
    value = getattr(item, "weapon_grip", None)
    if value is None and getattr(item, "template", None) is not None:
        value = getattr(item.template, "weapon_grip", "")
    grip = str(value or "").strip()
    if grip:
        return grip
    equipment_type = _item_equipment_type(item)
    if equipment_type == constants.EQUIPMENT_TYPE_WEAPON_1H:
        return constants.WEAPON_GRIP_ONE_HAND
    if equipment_type == constants.EQUIPMENT_TYPE_WEAPON_2H:
        return constants.WEAPON_GRIP_TWO_HANDS
    return ""


def _actor_trait_instances(actor: Any) -> list[dict[str, Any]]:
    traits = getattr(actor, "trait_instances", []) or []
    return traits if isinstance(traits, list) else []


def _actor_class_features(actor: Any, world: Any | None) -> dict[str, Any]:
    from core.stat_system import get_world_stat_system

    archetype = str(getattr(actor, "archetype", "") or "").strip()
    if not archetype:
        return {}
    stat_system = get_world_stat_system(world)
    profile = (stat_system.get("class_profiles") or {}).get(archetype) or {}
    features = profile.get("features") or {}
    return features if isinstance(features, dict) else {}


def _offhand_weapon_feature(actor: Any, world: Any | None) -> dict[str, Any]:
    features = _actor_class_features(actor, world)
    equipment_features = features.get("equipment") or {}
    if not isinstance(equipment_features, dict):
        equipment_features = {}

    merged = dict(equipment_features)
    for trait in _actor_trait_instances(actor):
        params = trait.get("params") or {}
        equipment = params.get("equipment") or {}
        if isinstance(equipment, dict):
            merged.update(equipment)
    return merged


def _raw_world_equipment_system(world: Any | None) -> dict[str, Any]:
    if world is None:
        return {}
    effective_config = getattr(world, "effective_config", None)
    config_obj = effective_config or getattr(world, "config", None)
    if config_obj is None:
        return {}
    value = getattr(config_obj, "equipment_system", None)
    return value if isinstance(value, dict) else {}


def can_actor_equip_offhand_weapon(actor: Any, item: Any) -> EquipmentPolicyResult:
    if _item_equipment_type(item) != constants.EQUIPMENT_TYPE_WEAPON_1H:
        return EquipmentPolicyResult(
            allowed=False,
            message="Only one-handed weapons can be equipped in the offhand slot.",
            code="offhand_weapon_grip_required",
        )

    world = getattr(actor, "world", None)
    equipment_system = get_world_equipment_system(world)
    offhand_policy = equipment_system.get("offhand_weapons") or {}
    allowed = bool(offhand_policy.get("default_allowed"))
    allowed_grips = list(
        offhand_policy.get("allowed_grips")
        or [constants.WEAPON_GRIP_ONE_HAND]
    )

    features = _offhand_weapon_feature(actor, world)
    if "can_equip_offhand_weapon" in features:
        allowed = bool(features.get("can_equip_offhand_weapon"))
    elif (
        not has_authored_offhand_weapon_policy(_raw_world_equipment_system(world))
        and getattr(actor, "archetype", None) == constants.ARCHETYPE_ASSASSIN
    ):
        # Compatibility for existing WR1-style content before WR2 offhand policy
        # is authored explicitly.
        allowed = True

    if "allowed_offhand_weapon_grips" in features:
        raw_grips = features.get("allowed_offhand_weapon_grips") or []
        if isinstance(raw_grips, list):
            allowed_grips = [str(grip or "").strip() for grip in raw_grips if str(grip or "").strip()]

    if not allowed:
        return EquipmentPolicyResult(
            allowed=False,
            message="You cannot wield a weapon in your offhand.",
            code="offhand_weapon_not_allowed",
        )

    grip = _item_weapon_grip(item)
    if grip not in set(allowed_grips):
        return EquipmentPolicyResult(
            allowed=False,
            message="You cannot wield that weapon in your offhand.",
            code="offhand_weapon_grip_required",
        )
    return EquipmentPolicyResult(allowed=True)


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
