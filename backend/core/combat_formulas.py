"""
Manifest-authored combat formula support for WR2.

This module resolves combat output from already-computed canonical stats. Stat
derivation remains in core.stat_system; this layer answers how attack_power,
ability_power, weapon damage, dodge, crit, armor, and resilience become a
single combat result.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import random
import re
from typing import Any, Callable

from config import constants
from config import game_settings as config
from core.stat_system import compute_stats


class CombatFormulaValidationError(ValueError):
    pass


SUPPORTED_COMBAT_VERSION = 1
PROFILE_KINDS = ("damage", "healing")
RATING_TYPES = ("mitigation_curve", "linear_rating", "percentage_points")
PROFILE_VARIANCE_STRATEGIES = ("default", "none")
LEVEL_SCALE_TYPES = ("exponential", "linear", "flat", "ilf")
ALLOWED_POWER_STATS = (
    "attack_power",
    "ability_power",
    "weapon_damage",
    "armor",
    "crit",
    "dodge",
    "resilience",
    "health_max",
    "energy_max",
    "stamina_max",
)
SNAPSHOT_STAT_KEYS = (
    "attack_power",
    "ability_power",
    "weapon_damage",
    "armor",
    "crit",
    "dodge",
    "resilience",
    "health_max",
    "energy_max",
    "stamina_max",
)
PROFILE_FIELDS = {
    "kind",
    "power_stat",
    "power_scale",
    "use_weapon_damage",
    "weapon_damage_scale",
    "unarmed_power_scale",
    "mob_unarmed_level_scale",
    "multiplier",
    "damage_type",
    "can_dodge",
    "can_crit",
    "crit_multiplier",
    "mitigation",
    "variance",
    "minimum",
}
COMBAT_SYSTEM_FIELDS = {
    "version",
    "default_attack_profile",
    "default_ability_profile",
    "default_healing_profile",
    "level_scale",
    "variance",
    "ratings",
    "profiles",
}


DEFAULT_COMBAT_SYSTEM: dict[str, Any] = {
    "version": SUPPORTED_COMBAT_VERSION,
    "default_attack_profile": "basic_physical",
    "default_ability_profile": "basic_ability",
    "default_healing_profile": "basic_heal",
    "level_scale": {
        "type": "exponential",
        "base": 5.5,
        "growth": 1.1,
    },
    "variance": {
        "enabled": True,
        "percent": 12.5,
    },
    "ratings": {
        "dodge": {
            "stat": "dodge",
            "type": "mitigation_curve",
            "base": 0.02,
            "constant": 60,
            "cap": 0.75,
        },
        "crit": {
            "stat": "crit",
            "type": "linear_rating",
            "base": 0.02,
            "constant": 120,
            "cap": 1.0,
        },
        "armor": {
            "stat": "armor",
            "type": "mitigation_curve",
            "base": 0,
            "constant": 60,
            "cap": 0.75,
        },
        "resilience": {
            "stat": "resilience",
            "type": "mitigation_curve",
            "base": 0,
            "constant": 120,
            "cap": 0.75,
        },
    },
    "profiles": {
        "basic_physical": {
            "kind": "damage",
            "power_stat": "attack_power",
            "power_scale": 0.0625,
            "use_weapon_damage": True,
            "weapon_damage_scale": 1.0,
            "unarmed_power_scale": 0.25,
            "mob_unarmed_level_scale": 0.5,
            "multiplier": 1.0,
            "damage_type": "physical",
            "can_dodge": True,
            "can_crit": True,
            "crit_multiplier": 1.5,
            "mitigation": {
                "armor": True,
                "resilience": False,
            },
            "variance": "default",
            "minimum": 1,
        },
        "basic_ability": {
            "kind": "damage",
            "power_stat": "ability_power",
            "power_scale": 0.1,
            "use_weapon_damage": False,
            "weapon_damage_scale": 0,
            "unarmed_power_scale": 0,
            "mob_unarmed_level_scale": 0,
            "multiplier": 1.0,
            "damage_type": "ability",
            "can_dodge": False,
            "can_crit": True,
            "crit_multiplier": 1.5,
            "mitigation": {
                "armor": False,
                "resilience": True,
            },
            "variance": "default",
            "minimum": 1,
        },
        "basic_heal": {
            "kind": "healing",
            "power_stat": "ability_power",
            "power_scale": 0.1,
            "use_weapon_damage": False,
            "weapon_damage_scale": 0,
            "unarmed_power_scale": 0,
            "mob_unarmed_level_scale": 0,
            "multiplier": 1.25,
            "damage_type": "healing",
            "can_dodge": False,
            "can_crit": True,
            "crit_multiplier": 1.5,
            "mitigation": {
                "armor": False,
                "resilience": False,
            },
            "variance": "default",
            "minimum": 1,
        },
    },
}


@dataclass(frozen=True)
class CombatantSnapshot:
    actor_type: str
    level: int
    stats: dict[str, float]
    weapon_damage: float


@dataclass(frozen=True)
class CombatAttackResult:
    profile: str
    damage_type: str
    outcome: str
    damage_base: int
    damage_dealt: int
    damage_taken: int
    damage_mitigated: int
    damage_absorbed: int
    healing_done: int
    is_crit_hit: bool
    is_heal: bool
    dodge_chance: float
    crit_chance: float
    armor_mitigation: float
    resilience_mitigation: float

    def event_data(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "damage_type": self.damage_type,
            "outcome": self.outcome,
            "damage_base": self.damage_base,
            "damage_dealt": self.damage_dealt,
            "damage_taken": self.damage_taken,
            "damage_mitigated": self.damage_mitigated,
            "damage_absorbed": self.damage_absorbed,
            "healing_done": self.healing_done,
            "is_crit_hit": self.is_crit_hit,
            "is_heal": self.is_heal,
            "dodge_chance": round(self.dodge_chance, 4),
            "crit_chance": round(self.crit_chance, 4),
            "armor_mitigation": round(self.armor_mitigation, 4),
            "resilience_mitigation": round(self.resilience_mitigation, 4),
        }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coerce_number(
    value: Any,
    *,
    field_name: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if not _is_number(value):
        raise CombatFormulaValidationError(f"{field_name} must be a number.")
    normalized = float(value)
    if minimum is not None and normalized < minimum:
        raise CombatFormulaValidationError(f"{field_name} must be >= {minimum}.")
    if maximum is not None and normalized > maximum:
        raise CombatFormulaValidationError(f"{field_name} must be <= {maximum}.")
    return normalized


def _coerce_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise CombatFormulaValidationError(f"{field_name} must be true or false.")
    return value


def _coerce_slug(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = str(value if value is not None else "").strip()
    if not text and allow_empty:
        return ""
    if not text or not re.match(r"^[a-z][a-z0-9_]*$", text):
        raise CombatFormulaValidationError(
            f"{field_name} must be a lowercase slug using letters, numbers, and underscores."
        )
    return text


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _coerce_level_scale(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise CombatFormulaValidationError("combat.level_scale must be a mapping.")
    scale_type = str(value.get("type") or DEFAULT_COMBAT_SYSTEM["level_scale"]["type"]).strip()
    if scale_type not in LEVEL_SCALE_TYPES:
        supported = ", ".join(LEVEL_SCALE_TYPES)
        raise CombatFormulaValidationError(
            f"combat.level_scale.type must be one of: {supported}."
        )
    if scale_type == "ilf":
        return {"type": scale_type}
    if scale_type == "exponential":
        return {
            "type": scale_type,
            "base": _coerce_number(
                value.get("base", DEFAULT_COMBAT_SYSTEM["level_scale"]["base"]),
                field_name="combat.level_scale.base",
                minimum=0.0001,
            ),
            "growth": _coerce_number(
                value.get("growth", DEFAULT_COMBAT_SYSTEM["level_scale"]["growth"]),
                field_name="combat.level_scale.growth",
                minimum=0.0001,
            ),
        }
    if scale_type == "linear":
        return {
            "type": scale_type,
            "base": _coerce_number(
                value.get("base", 5.5),
                field_name="combat.level_scale.base",
                minimum=0,
            ),
            "per_level": _coerce_number(
                value.get("per_level", 1.25),
                field_name="combat.level_scale.per_level",
                minimum=0,
            ),
        }
    return {
        "type": scale_type,
        "value": _coerce_number(
            value.get("value", 1.0),
            field_name="combat.level_scale.value",
            minimum=0,
        ),
    }


def _coerce_variance_block(value: Any, *, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise CombatFormulaValidationError(f"{field_name} must be a mapping.")
    enabled = _coerce_bool(value.get("enabled", True), field_name=f"{field_name}.enabled")
    percent = _coerce_number(
        value.get("percent", 12.5),
        field_name=f"{field_name}.percent",
        minimum=0,
        maximum=100,
    )
    return {
        "enabled": enabled,
        "percent": percent,
    }


def _coerce_profile_variance(value: Any, *, field_name: str) -> str | dict[str, Any]:
    if value in (None, ""):
        return "default"
    if isinstance(value, bool):
        return "default" if value else "none"
    if isinstance(value, str):
        strategy = value.strip()
        if strategy not in PROFILE_VARIANCE_STRATEGIES:
            raise CombatFormulaValidationError(
                f"{field_name} must be default, none, true, false, or a variance mapping."
            )
        return strategy
    if isinstance(value, dict):
        return _coerce_variance_block(value, field_name=field_name)
    raise CombatFormulaValidationError(
        f"{field_name} must be default, none, true, false, or a variance mapping."
    )


def _coerce_rating(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CombatFormulaValidationError(f"{field_name} must be a mapping.")
    stat = str(value.get("stat") or "").strip()
    if stat not in ALLOWED_POWER_STATS:
        raise CombatFormulaValidationError(
            f"{field_name}.stat must be a canonical combat stat."
        )
    rating_type = str(value.get("type") or "").strip()
    if rating_type not in RATING_TYPES:
        raise CombatFormulaValidationError(
            f"{field_name}.type must be one of {', '.join(RATING_TYPES)}."
        )
    normalized = {
        "stat": stat,
        "type": rating_type,
        "base": _coerce_number(value.get("base", 0), field_name=f"{field_name}.base"),
        "cap": _coerce_number(
            value.get("cap", 1),
            field_name=f"{field_name}.cap",
            minimum=0,
            maximum=1,
        ),
    }
    if rating_type != "percentage_points":
        normalized["constant"] = _coerce_number(
            value.get("constant"),
            field_name=f"{field_name}.constant",
            minimum=0.0001,
        )
    return normalized


def _coerce_ratings(value: Any) -> dict[str, Any]:
    ratings = deepcopy(DEFAULT_COMBAT_SYSTEM["ratings"])
    if value not in (None, ""):
        if not isinstance(value, dict):
            raise CombatFormulaValidationError("combat.ratings must be a mapping.")
        ratings = _merge_dict(ratings, value)

    normalized: dict[str, Any] = {}
    for raw_key, raw_value in ratings.items():
        key = _coerce_slug(raw_key, field_name="combat.ratings key")
        normalized[key] = _coerce_rating(
            raw_value,
            field_name=f"combat.ratings.{key}",
        )
    return normalized


def _coerce_mitigation_map(
    value: Any,
    *,
    field_name: str,
    rating_keys: set[str],
) -> dict[str, bool]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise CombatFormulaValidationError(f"{field_name} must be a mapping.")

    normalized: dict[str, bool] = {}
    for raw_key, raw_enabled in value.items():
        key = str(raw_key or "").strip()
        if key not in rating_keys:
            raise CombatFormulaValidationError(
                f"{field_name}.{key} must reference a declared rating rule."
            )
        normalized[key] = _coerce_bool(
            raw_enabled,
            field_name=f"{field_name}.{key}",
        )
    return normalized


def _coerce_profile(
    value: Any,
    *,
    field_name: str,
    rating_keys: set[str],
    default_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise CombatFormulaValidationError(f"{field_name} must be a mapping.")

    unknown_fields = sorted(set(value.keys()) - PROFILE_FIELDS)
    if unknown_fields:
        raise CombatFormulaValidationError(
            f"{field_name} has unsupported field(s): {', '.join(unknown_fields)}."
        )

    profile = _merge_dict(default_profile or {}, value)
    missing_fields = sorted(PROFILE_FIELDS - set(profile.keys()))
    if missing_fields:
        raise CombatFormulaValidationError(
            f"{field_name} is missing required field(s): {', '.join(missing_fields)}."
        )

    kind = str(profile.get("kind") or "").strip()
    if kind not in PROFILE_KINDS:
        raise CombatFormulaValidationError(
            f"{field_name}.kind must be one of {', '.join(PROFILE_KINDS)}."
        )
    power_stat = str(profile.get("power_stat") or "").strip()
    if power_stat not in ALLOWED_POWER_STATS:
        raise CombatFormulaValidationError(
            f"{field_name}.power_stat must be a canonical combat stat."
        )

    damage_type = str(profile.get("damage_type") or "").strip()
    if not damage_type:
        raise CombatFormulaValidationError(f"{field_name}.damage_type is required.")

    return {
        "kind": kind,
        "power_stat": power_stat,
        "power_scale": _coerce_number(
            profile.get("power_scale"),
            field_name=f"{field_name}.power_scale",
        ),
        "use_weapon_damage": _coerce_bool(
            profile.get("use_weapon_damage"),
            field_name=f"{field_name}.use_weapon_damage",
        ),
        "weapon_damage_scale": _coerce_number(
            profile.get("weapon_damage_scale"),
            field_name=f"{field_name}.weapon_damage_scale",
        ),
        "unarmed_power_scale": _coerce_number(
            profile.get("unarmed_power_scale"),
            field_name=f"{field_name}.unarmed_power_scale",
            minimum=0,
        ),
        "mob_unarmed_level_scale": _coerce_number(
            profile.get("mob_unarmed_level_scale"),
            field_name=f"{field_name}.mob_unarmed_level_scale",
            minimum=0,
        ),
        "multiplier": _coerce_number(
            profile.get("multiplier"),
            field_name=f"{field_name}.multiplier",
        ),
        "damage_type": damage_type,
        "can_dodge": _coerce_bool(
            profile.get("can_dodge"),
            field_name=f"{field_name}.can_dodge",
        ),
        "can_crit": _coerce_bool(
            profile.get("can_crit"),
            field_name=f"{field_name}.can_crit",
        ),
        "crit_multiplier": _coerce_number(
            profile.get("crit_multiplier"),
            field_name=f"{field_name}.crit_multiplier",
            minimum=0,
        ),
        "mitigation": _coerce_mitigation_map(
            profile.get("mitigation"),
            field_name=f"{field_name}.mitigation",
            rating_keys=rating_keys,
        ),
        "variance": _coerce_profile_variance(
            profile.get("variance"),
            field_name=f"{field_name}.variance",
        ),
        "minimum": _coerce_number(
            profile.get("minimum"),
            field_name=f"{field_name}.minimum",
            minimum=0,
        ),
    }


def _coerce_profiles(value: Any, *, rating_keys: set[str]) -> dict[str, Any]:
    profiles = deepcopy(DEFAULT_COMBAT_SYSTEM["profiles"])
    if value not in (None, ""):
        if not isinstance(value, dict):
            raise CombatFormulaValidationError("combat.profiles must be a mapping.")
        for raw_key, raw_profile in value.items():
            key = _coerce_slug(raw_key, field_name="combat.profiles key")
            if not isinstance(raw_profile, dict):
                raise CombatFormulaValidationError(
                    f"combat.profiles.{key} must be a mapping."
                )
            profiles[key] = _merge_dict(profiles.get(key, {}), raw_profile)

    normalized: dict[str, Any] = {}
    for key, profile in profiles.items():
        normalized[key] = _coerce_profile(
            profile,
            field_name=f"combat.profiles.{key}",
            rating_keys=rating_keys,
            default_profile=None,
        )
    return normalized


def normalize_combat_system(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise CombatFormulaValidationError("combat must be a mapping.")
    unknown_fields = sorted(set(value.keys()) - COMBAT_SYSTEM_FIELDS)
    if unknown_fields:
        raise CombatFormulaValidationError(
            f"combat has unsupported field(s): {', '.join(unknown_fields)}."
        )

    version = value.get("version", DEFAULT_COMBAT_SYSTEM["version"])
    if not isinstance(version, int) or isinstance(version, bool):
        raise CombatFormulaValidationError("combat.version must be an integer.")
    if version != SUPPORTED_COMBAT_VERSION:
        raise CombatFormulaValidationError(
            f"combat.version must be {SUPPORTED_COMBAT_VERSION}."
        )

    ratings = _coerce_ratings(value.get("ratings"))
    profiles = _coerce_profiles(value.get("profiles"), rating_keys=set(ratings))
    normalized = {
        "version": version,
        "default_attack_profile": _coerce_slug(
            value.get(
                "default_attack_profile",
                DEFAULT_COMBAT_SYSTEM["default_attack_profile"],
            ),
            field_name="combat.default_attack_profile",
        ),
        "default_ability_profile": _coerce_slug(
            value.get(
                "default_ability_profile",
                DEFAULT_COMBAT_SYSTEM["default_ability_profile"],
            ),
            field_name="combat.default_ability_profile",
        ),
        "default_healing_profile": _coerce_slug(
            value.get(
                "default_healing_profile",
                DEFAULT_COMBAT_SYSTEM["default_healing_profile"],
            ),
            field_name="combat.default_healing_profile",
        ),
        "level_scale": _coerce_level_scale(value.get("level_scale")),
        "variance": _coerce_variance_block(
            value.get("variance", DEFAULT_COMBAT_SYSTEM["variance"]),
            field_name="combat.variance",
        ),
        "ratings": ratings,
        "profiles": profiles,
    }

    for field_name in (
        "default_attack_profile",
        "default_ability_profile",
        "default_healing_profile",
    ):
        profile_key = normalized[field_name]
        if profile_key not in profiles:
            raise CombatFormulaValidationError(
                f"combat.{field_name} must reference a declared profile."
            )

    return normalized


def get_world_combat_system(world) -> dict[str, Any]:
    if world is None:
        return deepcopy(DEFAULT_COMBAT_SYSTEM)
    effective_config = getattr(world, "effective_config", None)
    config_obj = effective_config or getattr(world, "config", None)
    if config_obj is None:
        return deepcopy(DEFAULT_COMBAT_SYSTEM)
    return normalize_combat_system(getattr(config_obj, "combat_system", None))


def _level_scale(level: int, combat_system: dict[str, Any]) -> float:
    normalized_level = max(1, int(level or 1))
    level_scale = combat_system.get("level_scale", {})
    scale_type = level_scale.get("type", DEFAULT_COMBAT_SYSTEM["level_scale"]["type"])
    if scale_type == "ilf":
        return float(config.ILF(normalized_level))
    if scale_type == "linear":
        return float(level_scale.get("base", 5.5)) + (
            float(level_scale.get("per_level", 1.25)) * normalized_level
        )
    if scale_type == "flat":
        return float(level_scale.get("value", 1.0))
    return float(level_scale.get("base", 5.5)) * (
        float(level_scale.get("growth", 1.1)) ** normalized_level
    )


def _actor_type(actor: Any) -> str:
    class_name = actor.__class__.__name__.lower()
    if class_name == "player" or hasattr(actor, "user_id"):
        return "player"
    if class_name == "mob" or hasattr(actor, "template_id"):
        return "mob"
    return class_name or "actor"


def _numeric_attr(obj: Any, field_name: str) -> float:
    value = getattr(obj, field_name, 0)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _item_stat(item: Any, field_name: str) -> float:
    if item is None:
        return 0.0
    value = getattr(item, field_name, None)
    if value is None and getattr(item, "template", None) is not None:
        value = getattr(item.template, field_name, 0)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iter_equipment_items(actor: Any):
    equipment = getattr(actor, "equipment", None)
    if not equipment:
        return
    for slot in constants.EQUIPMENT_SLOTS:
        item = getattr(equipment, slot, None)
        if item is not None:
            yield item


def _equipped_weapon(actor: Any) -> Any | None:
    equipment = getattr(actor, "equipment", None)
    if not equipment:
        return None
    return getattr(equipment, "weapon", None)


def _weapon_damage(actor: Any) -> float:
    return max(0.0, _item_stat(_equipped_weapon(actor), "weapon_damage"))


def _player_snapshot(actor: Any, world: Any) -> CombatantSnapshot:
    stats = compute_stats(
        actor.level,
        actor.archetype,
        char=actor,
        world=world,
    )
    snapshot_stats = {
        key: float(stats.get(key) or 0)
        for key in SNAPSHOT_STAT_KEYS
        if key != "weapon_damage"
    }
    snapshot_stats["weapon_damage"] = _weapon_damage(actor)
    return CombatantSnapshot(
        actor_type="player",
        level=max(1, int(getattr(actor, "level", 1) or 1)),
        stats=snapshot_stats,
        weapon_damage=snapshot_stats["weapon_damage"],
    )


def _mob_snapshot(actor: Any) -> CombatantSnapshot:
    snapshot_stats = {
        "attack_power": _numeric_attr(actor, "attack_power"),
        "ability_power": _numeric_attr(actor, "ability_power"),
        "weapon_damage": _weapon_damage(actor),
        "armor": _numeric_attr(actor, "armor"),
        "crit": _numeric_attr(actor, "crit"),
        "dodge": _numeric_attr(actor, "dodge"),
        "resilience": _numeric_attr(actor, "resilience"),
        "health_max": _numeric_attr(actor, "health_max"),
        "energy_max": _numeric_attr(actor, "energy_max"),
        "stamina_max": _numeric_attr(actor, "stamina_max"),
    }
    for item in _iter_equipment_items(actor) or ():
        for stat_key in (
            "attack_power",
            "ability_power",
            "armor",
            "crit",
            "dodge",
            "resilience",
            "health_max",
            "energy_max",
            "stamina_max",
        ):
            snapshot_stats[stat_key] += _item_stat(item, stat_key)
    return CombatantSnapshot(
        actor_type="mob",
        level=max(1, int(getattr(actor, "level", 1) or 1)),
        stats=snapshot_stats,
        weapon_damage=snapshot_stats["weapon_damage"],
    )


def combatant_snapshot(actor: Any, *, world: Any | None = None) -> CombatantSnapshot:
    runtime_world = world or getattr(actor, "world", None)
    if _actor_type(actor) == "player":
        return _player_snapshot(actor, runtime_world)
    return _mob_snapshot(actor)


def _stat_value(snapshot: CombatantSnapshot, stat_key: str) -> float:
    return float(snapshot.stats.get(stat_key, 0.0) or 0.0)


def _rating_percent(
    *,
    rating_config: dict[str, Any],
    rating: float,
    opponent_level: int,
    combat_system: dict[str, Any],
) -> float:
    base = float(rating_config["base"])
    cap = float(rating_config["cap"])

    if rating_config["type"] == "percentage_points":
        return min(cap, max(0.0, rating / 100.0 + base))

    constant = float(rating_config["constant"])
    opponent_scale = max(0.0001, _level_scale(opponent_level, combat_system))

    if rating_config["type"] == "linear_rating":
        value = rating / (opponent_scale * constant) + base
    else:
        numerator = rating + opponent_scale * constant * base
        denominator = rating + opponent_scale * constant
        value = numerator / denominator if denominator else 0

    return min(cap, max(0.0, value))


def rating_display_percent(
    *,
    rating_config: dict[str, Any],
    rating: float,
    opponent_level: int,
    combat_system: dict[str, Any],
) -> float:
    return _rating_percent(
        rating_config=rating_config,
        rating=rating,
        opponent_level=opponent_level,
        combat_system=combat_system,
    ) * 100.0


def _random_value(rng: Callable[[], float]) -> float:
    try:
        value = float(rng())
    except (TypeError, ValueError):
        return random.random()
    return min(0.999999, max(0.0, value))


def _variance_config(
    profile: dict[str, Any],
    combat_system: dict[str, Any],
) -> dict[str, Any]:
    profile_variance = profile.get("variance", "default")
    if profile_variance == "none":
        return {"enabled": False, "percent": 0.0}
    if profile_variance == "default":
        return combat_system["variance"]
    return profile_variance


def _apply_variance(
    output: float,
    *,
    profile: dict[str, Any],
    combat_system: dict[str, Any],
    rng: Callable[[], float],
) -> float:
    variance = _variance_config(profile, combat_system)
    if not variance.get("enabled"):
        return output
    percent = max(0.0, float(variance.get("percent") or 0.0)) / 100
    if percent <= 0:
        return output
    low = 1 - percent
    high = 1 + percent
    return output * (low + _random_value(rng) * (high - low))


def _base_output(
    *,
    actor_snapshot: CombatantSnapshot,
    profile: dict[str, Any],
    combat_system: dict[str, Any],
) -> float:
    power_value = _stat_value(actor_snapshot, profile["power_stat"])
    power_scale = float(profile["power_scale"])
    if not profile["use_weapon_damage"]:
        return power_value * power_scale

    if actor_snapshot.weapon_damage > 0:
        return (
            actor_snapshot.weapon_damage * float(profile["weapon_damage_scale"])
            + power_value * power_scale
        )

    if actor_snapshot.actor_type == "mob":
        return (
            _level_scale(actor_snapshot.level, combat_system)
            * float(profile["mob_unarmed_level_scale"])
            + power_value * power_scale
        )

    return power_value * float(profile["unarmed_power_scale"])


def resolve_attack(
    *,
    actor: Any,
    target: Any,
    world: Any | None = None,
    profile_key: str | None = None,
    overrides: dict[str, Any] | None = None,
    rng: Callable[[], float] | None = None,
) -> CombatAttackResult:
    runtime_world = world or getattr(actor, "world", None) or getattr(target, "world", None)
    combat_system = get_world_combat_system(runtime_world)
    profiles = combat_system["profiles"]
    selected_profile_key = profile_key or combat_system["default_attack_profile"]
    if selected_profile_key not in profiles:
        raise CombatFormulaValidationError(
            f"combat profile '{selected_profile_key}' is not configured."
        )

    profile = deepcopy(profiles[selected_profile_key])
    if overrides:
        profile = _coerce_profile(
            _merge_dict(profile, overrides),
            field_name="combat.profile_override",
            rating_keys=set(combat_system["ratings"]),
            default_profile=None,
        )

    rng = rng or random.random
    actor_snapshot = combatant_snapshot(actor, world=runtime_world)
    target_snapshot = combatant_snapshot(target, world=runtime_world)

    base = _base_output(
        actor_snapshot=actor_snapshot,
        profile=profile,
        combat_system=combat_system,
    )
    output = base * float(profile["multiplier"])

    dodge_chance = 0.0
    if profile["can_dodge"] and "dodge" in combat_system["ratings"]:
        dodge_config = combat_system["ratings"]["dodge"]
        dodge_chance = _rating_percent(
            rating_config=dodge_config,
            rating=_stat_value(target_snapshot, dodge_config["stat"]),
            opponent_level=actor_snapshot.level,
            combat_system=combat_system,
        )
        if _random_value(rng) < dodge_chance:
            return CombatAttackResult(
                profile=selected_profile_key,
                damage_type=profile["damage_type"],
                outcome="dodged",
                damage_base=max(0, int(math.ceil(base))),
                damage_dealt=0,
                damage_taken=0,
                damage_mitigated=0,
                damage_absorbed=0,
                healing_done=0,
                is_crit_hit=False,
                is_heal=profile["kind"] == "healing",
                dodge_chance=dodge_chance,
                crit_chance=0.0,
                armor_mitigation=0.0,
                resilience_mitigation=0.0,
            )

    output = _apply_variance(
        output,
        profile=profile,
        combat_system=combat_system,
        rng=rng,
    )

    crit_chance = 0.0
    is_crit = False
    if profile["can_crit"] and "crit" in combat_system["ratings"]:
        crit_config = combat_system["ratings"]["crit"]
        crit_chance = _rating_percent(
            rating_config=crit_config,
            rating=_stat_value(actor_snapshot, crit_config["stat"]),
            opponent_level=target_snapshot.level,
            combat_system=combat_system,
        )
        is_crit = _random_value(rng) < crit_chance
        if is_crit:
            output *= float(profile["crit_multiplier"])

    pre_mitigation = max(0, int(math.ceil(output)))
    if profile["kind"] == "healing":
        healing_done = pre_mitigation
        if healing_done > 0:
            healing_done = max(int(math.ceil(profile["minimum"])), healing_done)
        return CombatAttackResult(
            profile=selected_profile_key,
            damage_type=profile["damage_type"],
            outcome="hit",
            damage_base=max(0, int(math.ceil(base))),
            damage_dealt=0,
            damage_taken=0,
            damage_mitigated=0,
            damage_absorbed=0,
            healing_done=healing_done,
            is_crit_hit=is_crit,
            is_heal=True,
            dodge_chance=dodge_chance,
            crit_chance=crit_chance,
            armor_mitigation=0.0,
            resilience_mitigation=0.0,
        )

    mitigated_output = float(pre_mitigation)
    applied_mitigation: dict[str, float] = {}
    for mitigation_key, enabled in profile["mitigation"].items():
        if not enabled:
            continue
        rating_config = combat_system["ratings"][mitigation_key]
        mitigation = _rating_percent(
            rating_config=rating_config,
            rating=_stat_value(target_snapshot, rating_config["stat"]),
            opponent_level=actor_snapshot.level,
            combat_system=combat_system,
        )
        applied_mitigation[mitigation_key] = mitigation
        mitigated_output *= 1 - mitigation

    damage_taken = max(0, int(math.ceil(mitigated_output)))
    if pre_mitigation > 0:
        damage_taken = max(int(math.ceil(profile["minimum"])), damage_taken)
    damage_mitigated = max(0, pre_mitigation - damage_taken)

    return CombatAttackResult(
        profile=selected_profile_key,
        damage_type=profile["damage_type"],
        outcome="hit",
        damage_base=max(0, int(math.ceil(base))),
        damage_dealt=pre_mitigation,
        damage_taken=damage_taken,
        damage_mitigated=damage_mitigated,
        damage_absorbed=0,
        healing_done=0,
        is_crit_hit=is_crit,
        is_heal=False,
        dodge_chance=dodge_chance,
        crit_chance=crit_chance,
        armor_mitigation=applied_mitigation.get("armor", 0.0),
        resilience_mitigation=applied_mitigation.get("resilience", 0.0),
    )
