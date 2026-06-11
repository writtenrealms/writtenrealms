from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from builders import manifests as builder_manifests
from config import constants as adv_consts
from config import game_settings as adv_config
from core.combat_formulas import (
    _level_scale,
    _rating_percent,
    get_world_combat_system,
)
from core.leveling import clamp_level, get_world_leveling_config
from core.stat_system import get_world_stat_system


@dataclass(frozen=True)
class TypeModifier:
    health: float = 1.0
    attack_power: float = 1.0
    armor: float = 1.0
    dodge: float = 1.0
    crit: float = 1.0
    resilience: float = 1.0


TYPE_MODIFIERS: dict[str, TypeModifier] = {
    adv_consts.MOB_TYPE_BEAST: TypeModifier(
        health=1.1,
        attack_power=1.15,
        armor=0.75,
        dodge=1.1,
        crit=0.8,
        resilience=0.5,
    ),
    adv_consts.MOB_TYPE_CONSTRUCT: TypeModifier(
        health=1.2,
        attack_power=1.0,
        armor=1.4,
        dodge=0.5,
        resilience=1.1,
    ),
    adv_consts.MOB_TYPE_GIANT: TypeModifier(
        health=1.35,
        attack_power=1.2,
        armor=1.1,
        dodge=0.6,
        crit=0.8,
    ),
    adv_consts.MOB_TYPE_HUMANOID: TypeModifier(),
    adv_consts.MOB_TYPE_OOZE: TypeModifier(
        health=1.3,
        attack_power=0.85,
        armor=0.6,
        dodge=0.35,
        crit=0.5,
        resilience=1.1,
    ),
    adv_consts.MOB_TYPE_UNEAD: TypeModifier(
        health=1.15,
        attack_power=0.95,
        armor=1.05,
        dodge=0.75,
        resilience=1.1,
    ),
}


def _context_world(world):
    return world.instance_of if getattr(world, "instance_of_id", None) else world


def _ceil_stat(value: float, *, minimum: int = 0) -> int:
    return max(minimum, int(math.ceil(max(0.0, value))))


def _capitalize_sentence(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return f"{text[0].upper()}{text[1:]}"


def _slug_keywords(slug: str) -> str:
    return " ".join(token for token in str(slug or "").replace("_", "-").split("-") if token)


def _fallback_experience(level: int, scale: float) -> int:
    configured = adv_config.MOB_EXP.get(level)
    if configured is not None:
        return int(configured)
    return _ceil_stat(scale * max(1, level) * 4, minimum=1)


def _basic_attack_base(*, level: int, stats: dict[str, int], combat_system: dict[str, Any]) -> int:
    profiles = combat_system.get("profiles") or {}
    profile_key = combat_system.get("default_attack_profile")
    profile = profiles.get(profile_key) or {}
    power_stat = str(profile.get("power_stat") or "attack_power")
    power_value = float(stats.get(power_stat) or 0)
    power_scale = float(profile.get("power_scale") or 0)

    if not profile.get("use_weapon_damage", True):
        return _ceil_stat(power_value * power_scale)

    weapon_damage = float(stats.get("weapon_damage") or 0)
    if weapon_damage > 0:
        base = (
            weapon_damage * float(profile.get("weapon_damage_scale") or 0)
            + power_value * power_scale
        )
        return _ceil_stat(base)

    base = (
        _level_scale(level, combat_system)
        * float(profile.get("mob_unarmed_level_scale") or 0)
        + power_value * power_scale
    )
    return _ceil_stat(base)


def _rating_preview(
    *,
    rating_key: str,
    rating: int,
    level: int,
    combat_system: dict[str, Any],
) -> float:
    rating_config = (combat_system.get("ratings") or {}).get(rating_key)
    if not rating_config:
        return 0.0
    return round(
        _rating_percent(
            rating_config=rating_config,
            rating=float(rating or 0),
            opponent_level=level,
            combat_system=combat_system,
        ),
        4,
    )


def _diagnostics(*, world, stat_system: dict[str, Any]) -> list[str]:
    diagnostics = ["Generated direct mob stats; no attributes emitted."]
    if not stat_system.get("class_profiles"):
        diagnostics.append("World has no class profiles; using combat level scale only.")
    formulas = stat_system.get("formulas") or {}
    if not formulas.get("global_rules"):
        diagnostics.append("World has no global stat rules; generated stats are advisory defaults.")
    if not (formulas.get("base_resources") or {}).get("health"):
        diagnostics.append("World has no health resource formula; health was budgeted directly.")
    if getattr(world, "instance_of_id", None):
        diagnostics.append("Suggestions were generated from the parent world configuration.")
    return diagnostics


def _suggest_direct_stats(*, level: int, mob_type: str, scale: float) -> dict[str, int]:
    modifier = TYPE_MODIFIERS.get(mob_type, TypeModifier())
    return {
        "exp_worth": _fallback_experience(level, scale),
        "gold": _ceil_stat(scale) if mob_type == adv_consts.MOB_TYPE_HUMANOID else 0,
        "health_max": _ceil_stat(scale * 5.0 * modifier.health, minimum=1),
        "health_regen": 0,
        "energy_max": 0,
        "energy_regen": 0,
        "stamina_max": 0,
        "stamina_regen": 0,
        "regen_rate": 4,
        "attack_power": _ceil_stat(scale * modifier.attack_power, minimum=1),
        "weapon_damage": _ceil_stat(scale * 1.5 * modifier.attack_power, minimum=1),
        "ability_power": 0,
        "armor": _ceil_stat(scale * 0.35 * modifier.armor),
        "dodge": _ceil_stat(scale * 0.25 * modifier.dodge),
        "crit": _ceil_stat(scale * 0.15 * modifier.crit),
        "resilience": _ceil_stat(scale * 0.25 * modifier.resilience),
    }


def suggest_mob_definition_manifest(
    world,
    *,
    name: str,
    slug: str,
    mob_type: str,
    level: int,
) -> dict[str, Any]:
    context_world = _context_world(world)
    normalized_level = clamp_level(level, get_world_leveling_config(context_world))
    combat_system = get_world_combat_system(context_world)
    stat_system = get_world_stat_system(context_world)
    scale = _level_scale(normalized_level, combat_system)
    stats = _suggest_direct_stats(
        level=normalized_level,
        mob_type=mob_type,
        scale=scale,
    )
    keywords = _slug_keywords(slug)
    room_description = f"{_capitalize_sentence(name)} is here." if name else ""

    spec = {
        "description": "",
        "room_description": room_description,
        "notes": "",
        "keywords": keywords,
        "type": mob_type,
        "assists": False,
        "level": normalized_level,
        **stats,
        "fights_back": True,
        "is_invisible": False,
        "combat": {
            "attackable": True,
        },
        "attributes": {},
        "randomization": {
            "attributes": [],
        },
    }
    manifest = {
        "kind": builder_manifests.MOB_DEFINITION_MANIFEST_KIND,
        "metadata": {
            "slug": slug,
            "name": name,
        },
        "spec": spec,
    }
    combat_preview = {
        "basic_attack_damage": _basic_attack_base(
            level=normalized_level,
            stats=stats,
            combat_system=combat_system,
        ),
        "same_level_armor_mitigation": _rating_preview(
            rating_key="armor",
            rating=stats["armor"],
            level=normalized_level,
            combat_system=combat_system,
        ),
        "same_level_dodge_chance": _rating_preview(
            rating_key="dodge",
            rating=stats["dodge"],
            level=normalized_level,
            combat_system=combat_system,
        ),
        "same_level_crit_chance": _rating_preview(
            rating_key="crit",
            rating=stats["crit"],
            level=normalized_level,
            combat_system=combat_system,
        ),
        "same_level_resilience_mitigation": _rating_preview(
            rating_key="resilience",
            rating=stats["resilience"],
            level=normalized_level,
            combat_system=combat_system,
        ),
    }
    return {
        "manifest": manifest,
        "yaml": builder_manifests.manifest_to_yaml(manifest),
        "summary": {
            "level": normalized_level,
            "type": mob_type,
            "role": "standard",
            "estimated_power_level": normalized_level,
            "confidence": "medium" if stat_system.get("class_profiles") else "low",
        },
        "suggested_stats": stats,
        "combat_preview": combat_preview,
        "diagnostics": _diagnostics(world=world, stat_system=stat_system),
    }
