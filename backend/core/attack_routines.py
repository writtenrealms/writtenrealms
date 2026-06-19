from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import constants
from core.combat_formulas import get_world_combat_system
from core.equipment_system import can_actor_equip_offhand_weapon
from core.stat_system import get_world_stat_system


@dataclass(frozen=True)
class CombatStrike:
    source: str
    weapon_slot: str = constants.EQUIPMENT_SLOT_WEAPON
    damage_multiplier: float = 1.0
    attack: str = "attack"
    label: str = "Attack"


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _number(value: Any, *, default: float = 1.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _slug(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _actor_world(actor: Any, target: Any | None, world: Any | None) -> Any | None:
    return world or getattr(actor, "world", None) or getattr(target, "world", None)


def _class_features(actor: Any, world: Any | None) -> dict[str, Any]:
    archetype = str(getattr(actor, "archetype", "") or "").strip()
    if not archetype:
        return {}
    stat_system = get_world_stat_system(world)
    profile = (stat_system.get("class_profiles") or {}).get(archetype) or {}
    features = profile.get("features") or {}
    return features if isinstance(features, dict) else {}


def _actor_feature_payload(actor: Any) -> dict[str, Any]:
    for attr_name in ("features", "feature_grants", "combat_features"):
        features = getattr(actor, attr_name, None)
        if isinstance(features, dict):
            return features
    return {}


def _effect_is_active(effect: dict[str, Any]) -> bool:
    try:
        return int(effect.get("remaining_rounds") or 0) > 0
    except (TypeError, ValueError):
        return False


def _active_effect_routines(actor: Any) -> list[dict[str, Any]]:
    effects = getattr(actor, "active_effects", []) or []
    if not isinstance(effects, list):
        return []

    routines: list[dict[str, Any]] = []
    applied_stack_keys: set[str] = set()
    for effect in effects:
        if not isinstance(effect, dict) or not _effect_is_active(effect):
            continue
        stack_key = str(effect.get("stack_key") or "").strip().lower()
        if stack_key:
            if stack_key in applied_stack_keys:
                continue
            applied_stack_keys.add(stack_key)

        for primitive in effect.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            if primitive.get("type") != "combat_modifier":
                continue
            if primitive.get("phase") != "attack_routine":
                continue
            routine = primitive.get("attack_routine") or {}
            if isinstance(routine, dict):
                routines.append(routine)
    return routines


def _trait_routines(actor: Any) -> list[tuple[str, dict[str, Any]]]:
    traits = getattr(actor, "trait_instances", []) or []
    if not isinstance(traits, list):
        return []

    routines: list[tuple[str, dict[str, Any]]] = []
    for trait in traits:
        if not isinstance(trait, dict):
            continue
        params = trait.get("params") or {}
        if not isinstance(params, dict):
            continue
        routine = params.get("attack_routine") or params.get("combat_routine") or {}
        if isinstance(routine, dict):
            routines.append((_slug(trait.get("key"), default="trait"), routine))
    return routines


def _feature_combat_routine(features: dict[str, Any]) -> dict[str, Any]:
    combat = features.get("combat") or {}
    return combat if isinstance(combat, dict) else {}


def _strike_from_routine(
    routine: dict[str, Any],
    *,
    default_source: str,
    default_attack: str,
    default_label: str,
    default_weapon_slot: str = constants.EQUIPMENT_SLOT_WEAPON,
    default_multiplier: float = 1.0,
) -> CombatStrike:
    strike = routine.get("strike") or {}
    if not isinstance(strike, dict):
        strike = {}
    source = _slug(strike.get("source"), default=default_source)
    label = str(strike.get("label") or default_label).strip() or default_label
    weapon_slot = _slug(strike.get("weapon_slot"), default=default_weapon_slot)
    if weapon_slot not in {
        constants.EQUIPMENT_SLOT_WEAPON,
        constants.EQUIPMENT_SLOT_OFFHAND,
    }:
        weapon_slot = default_weapon_slot
    return CombatStrike(
        source=source,
        attack=_slug(strike.get("attack"), default=source or default_attack),
        label=label,
        weapon_slot=weapon_slot,
        damage_multiplier=_number(
            strike.get("damage_multiplier"),
            default=default_multiplier,
        ),
    )


def _mainhand_contributions(actor: Any, world: Any | None) -> list[tuple[int, CombatStrike]]:
    contributions: list[tuple[int, CombatStrike]] = []

    class_routine = _feature_combat_routine(_class_features(actor, world))
    class_count = _positive_int(class_routine.get("extra_mainhand_strikes"))
    if class_count:
        contributions.append(
            (
                class_count,
                _strike_from_routine(
                    class_routine,
                    default_source="class_extra_attack",
                    default_attack="class_extra_attack",
                    default_label="Extra Attack",
                ),
            )
        )

    actor_routine = _feature_combat_routine(_actor_feature_payload(actor))
    actor_count = _positive_int(actor_routine.get("extra_mainhand_strikes"))
    if actor_count:
        contributions.append(
            (
                actor_count,
                _strike_from_routine(
                    actor_routine,
                    default_source="feature_extra_attack",
                    default_attack="feature_extra_attack",
                    default_label="Extra Attack",
                ),
            )
        )

    for trait_key, routine in _trait_routines(actor):
        trait_count = _positive_int(routine.get("extra_mainhand_strikes"))
        if trait_count:
            contributions.append(
                (
                    trait_count,
                    _strike_from_routine(
                        routine,
                        default_source=trait_key,
                        default_attack=trait_key,
                        default_label="Extra Attack",
                    ),
                )
            )

    for routine in _active_effect_routines(actor):
        effect_count = _positive_int(routine.get("extra_mainhand_strikes"))
        if effect_count:
            contributions.append(
                (
                    effect_count,
                    _strike_from_routine(
                        routine,
                        default_source="effect_extra_attack",
                        default_attack="effect_extra_attack",
                        default_label="Extra Attack",
                    ),
                )
            )
    return contributions


def _offhand_weapon(actor: Any) -> Any | None:
    equipment = getattr(actor, "equipment", None)
    if not equipment:
        return None
    offhand = getattr(equipment, constants.EQUIPMENT_SLOT_OFFHAND, None)
    if not offhand:
        return None
    equipment_type = str(getattr(offhand, "equipment_type", "") or "").strip()
    if not equipment_type and getattr(offhand, "template", None) is not None:
        equipment_type = str(getattr(offhand.template, "equipment_type", "") or "").strip()
    if equipment_type != constants.EQUIPMENT_TYPE_WEAPON_1H:
        return None
    return offhand


def _has_mainhand_weapon(actor: Any) -> bool:
    equipment = getattr(actor, "equipment", None)
    return bool(equipment and getattr(equipment, constants.EQUIPMENT_SLOT_WEAPON, None))


def _dual_wield_strike(actor: Any, combat_system: dict[str, Any]) -> CombatStrike | None:
    dual_wield = ((combat_system.get("attack_routine") or {}).get("dual_wield") or {})
    if not dual_wield.get("enabled") or not dual_wield.get("grants_offhand_strike"):
        return None
    offhand = _offhand_weapon(actor)
    if not offhand or not _has_mainhand_weapon(actor):
        return None
    if not can_actor_equip_offhand_weapon(actor, offhand).allowed:
        return None
    return CombatStrike(
        source="dual_wield_offhand",
        attack="dual_wield_offhand",
        label="Offhand Strike",
        weapon_slot=str(dual_wield.get("offhand_weapon_slot") or constants.EQUIPMENT_SLOT_OFFHAND),
        damage_multiplier=_number(dual_wield.get("offhand_damage_multiplier"), default=0.5),
    )


def _trait_offhand_strikes(actor: Any) -> list[CombatStrike]:
    strikes: list[CombatStrike] = []
    for trait_key, routine in _trait_routines(actor):
        count = _positive_int(routine.get("extra_offhand_strikes"))
        if not count:
            continue
        multiplier = _number(routine.get("offhand_damage_multiplier"), default=0.5)
        for _ in range(count):
            strikes.append(
                _strike_from_routine(
                    routine,
                    default_source=trait_key,
                    default_attack=trait_key,
                    default_label="Offhand Strike",
                    default_weapon_slot=constants.EQUIPMENT_SLOT_OFFHAND,
                    default_multiplier=multiplier,
                )
            )
    return strikes


def resolve_attack_routine(
    *,
    actor: Any,
    target: Any | None = None,
    world: Any | None = None,
) -> list[CombatStrike]:
    runtime_world = _actor_world(actor, target, world)
    combat_system = get_world_combat_system(runtime_world)
    routine_config = combat_system.get("attack_routine") or {}

    base_count = _positive_int(routine_config.get("base_mainhand_strikes", 1))
    strikes: list[CombatStrike] = [
        CombatStrike(source="base", attack="attack", label="Attack")
        for _ in range(base_count)
    ]

    contributions = _mainhand_contributions(actor, runtime_world)
    stacking = routine_config.get("stacking") or {}
    mode = str(stacking.get("extra_mainhand_strikes") or "max").strip()
    max_primary = _positive_int(stacking.get("max_primary_strikes"))
    remaining_mainhand = None if max_primary <= 0 else max(0, max_primary - len(strikes))

    if mode == "sum":
        for count, strike in contributions:
            for _ in range(count):
                if remaining_mainhand is not None and remaining_mainhand <= 0:
                    break
                strikes.append(strike)
                if remaining_mainhand is not None:
                    remaining_mainhand -= 1
    elif contributions:
        count, strike = max(contributions, key=lambda item: item[0])
        if remaining_mainhand is not None:
            count = min(count, remaining_mainhand)
        strikes.extend(strike for _ in range(count))

    dual_wield_strike = _dual_wield_strike(actor, combat_system)
    if dual_wield_strike is not None:
        strikes.append(dual_wield_strike)
    strikes.extend(_trait_offhand_strikes(actor))

    return strikes
