from __future__ import annotations

import math
from typing import Any

from builders.balance.mob_suggestions import _suggest_direct_stats
from config import constants as adv_consts
from core.combat_formulas import (
    _level_scale,
    _rating_percent,
    get_world_combat_system,
)
from core.equipment_system import get_world_equipment_system
from core.stat_system import DIRECT_STAT_KEYS, compute_attribute_formula_stats


COMBAT_STAT_KEYS = (
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

CATEGORY_LABELS = {
    "offense": "Offense",
    "defense": "Defense",
    "sustain": "Sustain",
    "utility": "Utility",
}


def _context_world(world):
    return world.instance_of if getattr(world, "instance_of_id", None) else world


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _round(value: float, places: int = 2) -> float:
    return round(float(value or 0.0), places)


def _combat_profile(combat_system: dict[str, Any], key: str) -> dict[str, Any]:
    profiles = combat_system.get("profiles") or {}
    profile_key = combat_system.get(key)
    return profiles.get(profile_key) or {}


def _rating(
    *,
    combat_system: dict[str, Any],
    rating_key: str,
    rating: float,
    level: int,
) -> float:
    rating_config = (combat_system.get("ratings") or {}).get(rating_key)
    if not rating_config:
        return 0.0
    return _rating_percent(
        rating_config=rating_config,
        rating=rating,
        opponent_level=level,
        combat_system=combat_system,
    )


def _base_stat_map() -> dict[str, float]:
    return {key: 0.0 for key in COMBAT_STAT_KEYS}


def _definition_stats(
    *,
    world,
    base_properties: dict[str, Any],
    attributes: dict[str, Any],
    archetype: str | None = None,
    include_weapon_damage: bool = False,
) -> dict[str, float]:
    stats = _base_stat_map()
    for key in COMBAT_STAT_KEYS:
        stats[key] = _safe_float(base_properties.get(key), 0.0)

    formula_stats = compute_attribute_formula_stats(
        world=world,
        attributes=attributes or {},
        archetype=archetype,
    )
    for key in DIRECT_STAT_KEYS:
        stats[key] = stats.get(key, 0.0) + _safe_float(formula_stats.get(key), 0.0)

    if include_weapon_damage:
        stats["weapon_damage"] = _safe_float(base_properties.get("weapon_damage"), 0.0)
    return stats


def _category_totals(drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals = {key: 0.0 for key in CATEGORY_LABELS}
    for driver in drivers:
        category = driver["category"]
        totals[category] = totals.get(category, 0.0) + float(driver["score"])
    return [
        {
            "key": key,
            "label": label,
            "score": _round(totals.get(key, 0.0)),
        }
        for key, label in CATEGORY_LABELS.items()
    ]


def _add_driver(
    drivers: list[dict[str, Any]],
    *,
    category: str,
    stat: str,
    label: str,
    value: float,
    score: float,
    detail: str = "",
) -> None:
    if abs(score) < 0.0001 and abs(value) < 0.0001:
        return
    drivers.append(
        {
            "category": category,
            "stat": stat,
            "label": label,
            "value": _round(value),
            "score": _round(score),
            "detail": detail,
        }
    )


def _sorted_drivers(drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(drivers, key=lambda driver: abs(float(driver["score"])), reverse=True)


def _armor_class_multiplier(equipment_system: dict[str, Any], armor_class: str) -> float:
    key = str(armor_class or equipment_system.get("default_armor_class") or "").strip()
    for armor_spec in equipment_system.get("armor_classes") or []:
        if armor_spec.get("key") == key:
            return _safe_float(armor_spec.get("armor_multiplier"), 1.0)
    return 1.0


def _slot_weight(equipment_system: dict[str, Any], equipment_type: str) -> float:
    suggestions = equipment_system.get("armor_suggestions") or {}
    slot_weights = suggestions.get("slot_weights") or {}
    return _safe_float(slot_weights.get(equipment_type), 0.0)


def _expected_slot_armor(
    *,
    level: int,
    equipment_type: str,
    armor_class: str,
    equipment_system: dict[str, Any],
    combat_system: dict[str, Any],
) -> int:
    if equipment_type not in (*adv_consts.EQUIPMENT_ARMOR, adv_consts.EQUIPMENT_TYPE_SHIELD):
        return 0
    suggestions = equipment_system.get("armor_suggestions") or {}
    full_set_scale = _safe_float(suggestions.get("full_set_scale"), 0.35)
    slot_weight = _slot_weight(equipment_system, equipment_type)
    armor_multiplier = _armor_class_multiplier(equipment_system, armor_class)
    expected = _level_scale(level, combat_system) * full_set_scale * slot_weight * armor_multiplier
    return max(0, int(math.ceil(expected)))


def _item_score_components(
    *,
    level: int,
    stats: dict[str, float],
    combat_system: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    attack_profile = _combat_profile(combat_system, "default_attack_profile")
    ability_profile = _combat_profile(combat_system, "default_ability_profile")

    weapon_scale = _safe_float(attack_profile.get("weapon_damage_scale"), 1.0)
    attack_power_scale = _safe_float(attack_profile.get("power_scale"), 0.0)
    ability_power_scale = _safe_float(ability_profile.get("power_scale"), 0.0)
    crit_multiplier = _safe_float(attack_profile.get("crit_multiplier"), 1.5)

    weapon_damage = _safe_float(stats.get("weapon_damage"))
    attack_power = _safe_float(stats.get("attack_power"))
    ability_power = _safe_float(stats.get("ability_power"))
    health_max = _safe_float(stats.get("health_max"))
    health_regen = _safe_float(stats.get("health_regen"))
    energy_max = _safe_float(stats.get("energy_max"))
    energy_regen = _safe_float(stats.get("energy_regen"))
    stamina_max = _safe_float(stats.get("stamina_max"))
    stamina_regen = _safe_float(stats.get("stamina_regen"))

    crit_chance = _rating(
        combat_system=combat_system,
        rating_key="crit",
        rating=_safe_float(stats.get("crit")),
        level=level,
    )
    armor_mitigation = _rating(
        combat_system=combat_system,
        rating_key="armor",
        rating=_safe_float(stats.get("armor")),
        level=level,
    )
    dodge_chance = _rating(
        combat_system=combat_system,
        rating_key="dodge",
        rating=_safe_float(stats.get("dodge")),
        level=level,
    )
    resilience_mitigation = _rating(
        combat_system=combat_system,
        rating_key="resilience",
        rating=_safe_float(stats.get("resilience")),
        level=level,
    )

    _add_driver(
        drivers,
        category="offense",
        stat="weapon_damage",
        label="Weapon damage",
        value=weapon_damage,
        score=weapon_damage * weapon_scale * 10,
    )
    _add_driver(
        drivers,
        category="offense",
        stat="attack_power",
        label="Attack power",
        value=attack_power,
        score=attack_power * attack_power_scale * 10,
    )
    _add_driver(
        drivers,
        category="offense",
        stat="ability_power",
        label="Ability power",
        value=ability_power,
        score=ability_power * ability_power_scale * 10,
    )
    _add_driver(
        drivers,
        category="offense",
        stat="crit",
        label="Crit",
        value=_safe_float(stats.get("crit")),
        score=crit_chance * (crit_multiplier - 1) * 100,
        detail=f"{_round(crit_chance * 100)}%",
    )

    _add_driver(
        drivers,
        category="defense",
        stat="health_max",
        label="Health",
        value=health_max,
        score=health_max * 0.5,
    )
    _add_driver(
        drivers,
        category="defense",
        stat="armor",
        label="Armor",
        value=_safe_float(stats.get("armor")),
        score=armor_mitigation * 100,
        detail=f"{_round(armor_mitigation * 100)}%",
    )
    _add_driver(
        drivers,
        category="defense",
        stat="dodge",
        label="Dodge",
        value=_safe_float(stats.get("dodge")),
        score=dodge_chance * 75,
        detail=f"{_round(dodge_chance * 100)}%",
    )
    _add_driver(
        drivers,
        category="defense",
        stat="resilience",
        label="Resilience",
        value=_safe_float(stats.get("resilience")),
        score=resilience_mitigation * 75,
        detail=f"{_round(resilience_mitigation * 100)}%",
    )

    _add_driver(
        drivers,
        category="sustain",
        stat="health_regen",
        label="Health regen",
        value=health_regen,
        score=health_regen * 40,
    )
    _add_driver(
        drivers,
        category="sustain",
        stat="energy_regen",
        label="Energy regen",
        value=energy_regen,
        score=energy_regen * 20,
    )
    _add_driver(
        drivers,
        category="sustain",
        stat="stamina_regen",
        label="Stamina regen",
        value=stamina_regen,
        score=stamina_regen * 20,
    )
    _add_driver(
        drivers,
        category="utility",
        stat="energy_max",
        label="Energy",
        value=energy_max,
        score=energy_max * 0.25,
    )
    _add_driver(
        drivers,
        category="utility",
        stat="stamina_max",
        label="Stamina",
        value=stamina_max,
        score=stamina_max * 0.1,
    )

    base_attack_output = weapon_damage * weapon_scale + attack_power * attack_power_scale
    expected_attack_output = base_attack_output * (1 + crit_chance * max(0.0, crit_multiplier - 1))
    metrics = {
        "basic_attack_base": _round(base_attack_output),
        "expected_basic_attack": _round(expected_attack_output),
        "ability_output": _round(ability_power * ability_power_scale),
        "crit_chance": _round(crit_chance * 100),
        "armor_mitigation": _round(armor_mitigation * 100),
        "dodge_chance": _round(dodge_chance * 100),
        "resilience_mitigation": _round(resilience_mitigation * 100),
    }
    return drivers, metrics


def _reference_item_stats(
    *,
    level: int,
    equipment_type: str,
    armor_class: str,
    equipment_system: dict[str, Any],
    combat_system: dict[str, Any],
) -> dict[str, float]:
    stats = _base_stat_map()
    stats["weapon_damage"] = 0.0
    scale = _level_scale(level, combat_system)
    if equipment_type == adv_consts.EQUIPMENT_TYPE_WEAPON_2H:
        stats["weapon_damage"] = max(1.0, math.ceil(scale * 1.5))
    elif equipment_type == adv_consts.EQUIPMENT_TYPE_WEAPON_1H:
        stats["weapon_damage"] = max(1.0, math.ceil(scale))
    elif equipment_type in (*adv_consts.EQUIPMENT_ARMOR, adv_consts.EQUIPMENT_TYPE_SHIELD):
        stats["armor"] = _expected_slot_armor(
            level=level,
            equipment_type=equipment_type,
            armor_class=armor_class,
            equipment_system=equipment_system,
            combat_system=combat_system,
        )
    elif equipment_type == adv_consts.EQUIPMENT_TYPE_ACCESSORY:
        stats["health_max"] = math.ceil(scale)
        stats["resilience"] = math.ceil(scale * 0.25)
    else:
        stats["health_max"] = math.ceil(scale * 0.5)
    return stats


def _score_from_components(drivers: list[dict[str, Any]]) -> float:
    return sum(float(driver["score"]) for driver in drivers)


def _estimate_item_power_level(
    *,
    score: float,
    equipment_type: str,
    armor_class: str,
    equipment_system: dict[str, Any],
    combat_system: dict[str, Any],
    max_level: int,
) -> int:
    if score <= 0:
        return 0
    for candidate_level in range(1, max(1, max_level) + 1):
        reference_stats = _reference_item_stats(
            level=candidate_level,
            equipment_type=equipment_type,
            armor_class=armor_class,
            equipment_system=equipment_system,
            combat_system=combat_system,
        )
        reference_drivers, _ = _item_score_components(
            level=candidate_level,
            stats=reference_stats,
            combat_system=combat_system,
        )
        if _score_from_components(reference_drivers) >= score:
            return candidate_level
    return max_level


def _max_world_level(world, fallback: int) -> int:
    effective_config = getattr(world, "effective_config", None)
    config_obj = effective_config or getattr(world, "config", None)
    if config_obj is not None:
        leveling_curve = getattr(config_obj, "leveling_curve", None)
        if leveling_curve:
            return max(len(leveling_curve), fallback)
        max_level = getattr(config_obj, "max_level", None)
        if max_level:
            return max(_safe_int(max_level, fallback), fallback)
    return fallback


def analyze_item_definition_power(world, item_definition) -> dict[str, Any]:
    context_world = _context_world(world)
    combat_system = get_world_combat_system(context_world)
    equipment_system = get_world_equipment_system(context_world)
    base_properties = item_definition.base_properties or {}
    equipment_type = str(base_properties.get("equipment_type") or "").strip()
    armor_class = str(base_properties.get("armor_class") or "").strip()
    level = max(1, _safe_int(base_properties.get("level"), 1))
    max_level = max(_max_world_level(context_world, level), level)
    stats = _definition_stats(
        world=context_world,
        base_properties=base_properties,
        attributes=item_definition.attributes or {},
        include_weapon_damage=True,
    )
    drivers, metrics = _item_score_components(
        level=level,
        stats=stats,
        combat_system=combat_system,
    )
    score = _score_from_components(drivers)
    expected_armor = _expected_slot_armor(
        level=level,
        equipment_type=equipment_type,
        armor_class=armor_class,
        equipment_system=equipment_system,
        combat_system=combat_system,
    )
    reference_stats = _reference_item_stats(
        level=level,
        equipment_type=equipment_type,
        armor_class=armor_class,
        equipment_system=equipment_system,
        combat_system=combat_system,
    )
    reference_drivers, _ = _item_score_components(
        level=level,
        stats=reference_stats,
        combat_system=combat_system,
    )
    reference_score = _score_from_components(reference_drivers)
    estimated_power_level = _estimate_item_power_level(
        score=score,
        equipment_type=equipment_type,
        armor_class=armor_class,
        equipment_system=equipment_system,
        combat_system=combat_system,
        max_level=max_level,
    )
    diagnostics = ["Power analysis is advisory and does not change runtime stats."]
    if item_definition.randomization:
        diagnostics.append("Randomized ranges are not included; fixed stats are analyzed.")
    if equipment_type in (*adv_consts.EQUIPMENT_ARMOR, adv_consts.EQUIPMENT_TYPE_SHIELD) and expected_armor:
        armor = _safe_float(stats.get("armor"))
        if armor > expected_armor * 1.5:
            diagnostics.append("Armor is above the current slot-weight reference for this level.")
        elif armor < expected_armor * 0.5:
            diagnostics.append("Armor is below the current slot-weight reference for this level.")
    elif item_definition.item_type == adv_consts.ITEM_TYPE_EQUIPPABLE and not equipment_type:
        diagnostics.append("No equipment type is set, so slot comparison is limited.")

    return {
        "kind": "itemdefinition",
        "entity": {
            "id": item_definition.id,
            "key": item_definition.key,
            "slug": item_definition.slug,
            "name": item_definition.name or "",
        },
        "summary": {
            "level": level,
            "equipment_type": equipment_type,
            "armor_class": armor_class,
            "estimated_power_level": estimated_power_level,
            "budget_score": _round(score),
            "reference_score": _round(reference_score),
            "reference_ratio": _round(score / reference_score, 2) if reference_score else 0,
        },
        "categories": _category_totals(drivers),
        "drivers": _sorted_drivers(drivers),
        "metrics": {
            **metrics,
            "level_scale": _round(_level_scale(level, combat_system)),
            "slot_weight": _round(_slot_weight(equipment_system, equipment_type), 4),
            "expected_slot_armor": expected_armor,
            "armor_to_reference": (
                _round(_safe_float(stats.get("armor")) / expected_armor, 2)
                if expected_armor
                else 0
            ),
        },
        "diagnostics": diagnostics,
    }


def _mob_basic_attack_base(
    *,
    level: int,
    stats: dict[str, float],
    combat_system: dict[str, Any],
) -> float:
    profile = _combat_profile(combat_system, "default_attack_profile")
    attack_power = _safe_float(stats.get("attack_power"))
    power_scale = _safe_float(profile.get("power_scale"), 0.0)
    unarmed_level_scale = _safe_float(profile.get("mob_unarmed_level_scale"), 0.0)
    return _level_scale(level, combat_system) * unarmed_level_scale + attack_power * power_scale


def _mob_score_components(
    *,
    level: int,
    stats: dict[str, float],
    combat_system: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    attack_profile = _combat_profile(combat_system, "default_attack_profile")
    crit_multiplier = _safe_float(attack_profile.get("crit_multiplier"), 1.5)
    basic_base = _mob_basic_attack_base(
        level=level,
        stats=stats,
        combat_system=combat_system,
    )
    crit_chance = _rating(
        combat_system=combat_system,
        rating_key="crit",
        rating=_safe_float(stats.get("crit")),
        level=level,
    )
    armor_mitigation = _rating(
        combat_system=combat_system,
        rating_key="armor",
        rating=_safe_float(stats.get("armor")),
        level=level,
    )
    dodge_chance = _rating(
        combat_system=combat_system,
        rating_key="dodge",
        rating=_safe_float(stats.get("dodge")),
        level=level,
    )
    resilience_mitigation = _rating(
        combat_system=combat_system,
        rating_key="resilience",
        rating=_safe_float(stats.get("resilience")),
        level=level,
    )
    expected_attack = basic_base * (1 + crit_chance * max(0.0, crit_multiplier - 1))
    health_max = _safe_float(stats.get("health_max"))
    physical_ehp = health_max / max(0.05, 1 - armor_mitigation) / max(0.05, 1 - dodge_chance)
    ability_ehp = health_max / max(0.05, 1 - resilience_mitigation)

    _add_driver(
        drivers,
        category="offense",
        stat="attack_power",
        label="Basic attack",
        value=_safe_float(stats.get("attack_power")),
        score=expected_attack * 10,
        detail=f"{_round(expected_attack)} expected",
    )
    _add_driver(
        drivers,
        category="defense",
        stat="health_max",
        label="Health",
        value=health_max,
        score=health_max * 0.5,
    )
    _add_driver(
        drivers,
        category="defense",
        stat="armor",
        label="Physical mitigation",
        value=_safe_float(stats.get("armor")),
        score=max(0.0, physical_ehp - health_max) * 0.35,
        detail=f"{_round(armor_mitigation * 100)}%",
    )
    _add_driver(
        drivers,
        category="defense",
        stat="resilience",
        label="Ability mitigation",
        value=_safe_float(stats.get("resilience")),
        score=max(0.0, ability_ehp - health_max) * 0.2,
        detail=f"{_round(resilience_mitigation * 100)}%",
    )
    _add_driver(
        drivers,
        category="defense",
        stat="dodge",
        label="Dodge",
        value=_safe_float(stats.get("dodge")),
        score=dodge_chance * 100,
        detail=f"{_round(dodge_chance * 100)}%",
    )
    _add_driver(
        drivers,
        category="sustain",
        stat="health_regen",
        label="Health regen",
        value=_safe_float(stats.get("health_regen")),
        score=_safe_float(stats.get("health_regen")) * 40,
    )
    _add_driver(
        drivers,
        category="utility",
        stat="resources",
        label="Resources",
        value=_safe_float(stats.get("energy_max")) + _safe_float(stats.get("stamina_max")),
        score=(_safe_float(stats.get("energy_max")) * 0.1)
        + (_safe_float(stats.get("stamina_max")) * 0.05),
    )

    metrics = {
        "basic_attack_base": _round(basic_base),
        "expected_basic_attack": _round(expected_attack),
        "physical_effective_health": _round(physical_ehp),
        "ability_effective_health": _round(ability_ehp),
        "crit_chance": _round(crit_chance * 100),
        "armor_mitigation": _round(armor_mitigation * 100),
        "dodge_chance": _round(dodge_chance * 100),
        "resilience_mitigation": _round(resilience_mitigation * 100),
    }
    return drivers, metrics


def _estimate_mob_power_level(
    *,
    score: float,
    mob_type: str,
    combat_system: dict[str, Any],
    max_level: int,
) -> int:
    if score <= 0:
        return 0
    for candidate_level in range(1, max(1, max_level) + 1):
        scale = _level_scale(candidate_level, combat_system)
        reference_stats = {
            key: float(value)
            for key, value in _suggest_direct_stats(
                level=candidate_level,
                mob_type=mob_type,
                scale=scale,
            ).items()
        }
        reference_drivers, _ = _mob_score_components(
            level=candidate_level,
            stats=reference_stats,
            combat_system=combat_system,
        )
        if _score_from_components(reference_drivers) >= score:
            return candidate_level
    return max_level


def analyze_mob_definition_power(world, mob_definition) -> dict[str, Any]:
    context_world = _context_world(world)
    combat_system = get_world_combat_system(context_world)
    base_properties = mob_definition.base_properties or {}
    archetype = str(base_properties.get("archetype") or "").strip() or None
    level = max(1, _safe_int(base_properties.get("level"), 1))
    max_level = max(_max_world_level(context_world, level), level)
    stats = _definition_stats(
        world=context_world,
        base_properties=base_properties,
        attributes=mob_definition.attributes or {},
        archetype=archetype,
    )
    drivers, metrics = _mob_score_components(
        level=level,
        stats=stats,
        combat_system=combat_system,
    )
    score = _score_from_components(drivers)
    scale = _level_scale(level, combat_system)
    reference_stats = {
        key: float(value)
        for key, value in _suggest_direct_stats(
            level=level,
            mob_type=mob_definition.mob_type,
            scale=scale,
        ).items()
    }
    reference_drivers, _ = _mob_score_components(
        level=level,
        stats=reference_stats,
        combat_system=combat_system,
    )
    reference_score = _score_from_components(reference_drivers)
    diagnostics = ["Power analysis is advisory and does not change runtime stats."]
    if mob_definition.randomization:
        diagnostics.append("Randomized ranges are not included; fixed stats are analyzed.")
    if mob_definition.attributes:
        diagnostics.append("World stat formulas were applied to authored attributes.")

    return {
        "kind": "mobdefinition",
        "entity": {
            "id": mob_definition.id,
            "key": mob_definition.key,
            "slug": mob_definition.slug,
            "name": mob_definition.name or "",
        },
        "summary": {
            "level": level,
            "type": mob_definition.mob_type,
            "estimated_power_level": _estimate_mob_power_level(
                score=score,
                mob_type=mob_definition.mob_type,
                combat_system=combat_system,
                max_level=max_level,
            ),
            "budget_score": _round(score),
            "reference_score": _round(reference_score),
            "reference_ratio": _round(score / reference_score, 2) if reference_score else 0,
        },
        "categories": _category_totals(drivers),
        "drivers": _sorted_drivers(drivers),
        "metrics": {
            **metrics,
            "level_scale": _round(scale),
        },
        "diagnostics": diagnostics,
    }
