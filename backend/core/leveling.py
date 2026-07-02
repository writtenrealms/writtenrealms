from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import game_settings as config
from core.stat_system import compute_stats
from core.world_config import inherited_system_config


DEFAULT_STARTING_LEVEL = 1
DEFAULT_LEVELING_CURVE = tuple(int(value) for value in config.LEVEL_EXPERIENCE)
DEFAULT_MAX_LEVEL = len(DEFAULT_LEVELING_CURVE)


class LevelingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LevelingConfig:
    starting_level: int
    max_level: int
    leveling_curve: list[int]


@dataclass(frozen=True)
class LevelProgress:
    level: int
    experience: int
    experience_progress: int
    experience_needed: int
    max_level: int


@dataclass(frozen=True)
class ExperienceGrant:
    experience_gained: int
    previous_experience: int
    new_experience: int
    previous_level: int
    new_level: int
    experience_progress: int
    experience_needed: int
    max_level: int

    @property
    def levels_gained(self) -> int:
        return max(0, self.new_level - self.previous_level)

    @property
    def leveled_up(self) -> bool:
        return self.levels_gained > 0


def default_leveling_curve() -> list[int]:
    return list(DEFAULT_LEVELING_CURVE)


def _coerce_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise LevelingConfigError(f"{field_name} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise LevelingConfigError(f"{field_name} must be an integer.")


def normalize_leveling_curve(value: Any) -> list[int]:
    if value in (None, ""):
        return default_leveling_curve()
    if isinstance(value, dict):
        value = value.get("thresholds")
    if not isinstance(value, (list, tuple)):
        raise LevelingConfigError(
            "leveling_curve must be a list of cumulative XP thresholds."
        )
    if not value:
        raise LevelingConfigError("leveling_curve must include at least level 1.")

    thresholds: list[int] = []
    previous = None
    for index, raw_threshold in enumerate(value):
        threshold = _coerce_int(
            raw_threshold,
            field_name=f"leveling_curve[{index}]",
        )
        if threshold < 0:
            raise LevelingConfigError("leveling_curve thresholds must be >= 0.")
        if previous is not None and threshold <= previous:
            raise LevelingConfigError(
                "leveling_curve thresholds must strictly increase after level 1."
            )
        thresholds.append(threshold)
        previous = threshold

    if thresholds[0] != 0:
        raise LevelingConfigError("leveling_curve must start with 0 for level 1.")
    return thresholds


def validate_leveling_config(
    *,
    starting_level: Any,
    max_level: Any,
    leveling_curve: Any,
) -> LevelingConfig:
    curve = normalize_leveling_curve(leveling_curve)
    normalized_starting_level = _coerce_int(
        starting_level,
        field_name="starting_level",
    )
    normalized_max_level = _coerce_int(max_level, field_name="max_level")

    if normalized_starting_level < 1:
        raise LevelingConfigError("starting_level must be >= 1.")
    if normalized_max_level < 1:
        raise LevelingConfigError("max_level must be >= 1.")
    if normalized_max_level > len(curve):
        raise LevelingConfigError(
            "max_level cannot exceed the number of entries in leveling_curve."
        )
    if normalized_starting_level > normalized_max_level:
        raise LevelingConfigError("starting_level cannot exceed max_level.")

    return LevelingConfig(
        starting_level=normalized_starting_level,
        max_level=normalized_max_level,
        leveling_curve=curve,
    )


def get_leveling_config(config_obj: Any | None) -> LevelingConfig:
    try:
        return validate_leveling_config(
            starting_level=getattr(config_obj, "starting_level", DEFAULT_STARTING_LEVEL),
            max_level=getattr(config_obj, "max_level", DEFAULT_MAX_LEVEL),
            leveling_curve=getattr(config_obj, "leveling_curve", DEFAULT_LEVELING_CURVE),
        )
    except LevelingConfigError:
        return LevelingConfig(
            starting_level=DEFAULT_STARTING_LEVEL,
            max_level=DEFAULT_MAX_LEVEL,
            leveling_curve=default_leveling_curve(),
        )


def get_world_leveling_config(world: Any | None) -> LevelingConfig:
    return get_leveling_config(inherited_system_config(world))


def clamp_level(level: Any, config_obj: Any | None = None) -> int:
    leveling_config = get_leveling_config(config_obj)
    normalized_level = _coerce_int(level, field_name="level")
    if normalized_level < 1 or normalized_level > leveling_config.max_level:
        raise LevelingConfigError(
            f"level must be between 1 and {leveling_config.max_level}."
        )
    return normalized_level


def experience_for_level(level: Any, config_obj: Any | None = None) -> int:
    leveling_config = get_leveling_config(config_obj)
    normalized_level = max(1, min(int(level or 1), leveling_config.max_level))
    return int(leveling_config.leveling_curve[normalized_level - 1])


def level_for_experience(experience: Any, config_obj: Any | None = None) -> int:
    leveling_config = get_leveling_config(config_obj)
    try:
        total_experience = int(experience or 0)
    except (TypeError, ValueError):
        total_experience = 0

    level = 1
    for index, threshold in enumerate(leveling_config.leveling_curve[:leveling_config.max_level]):
        if total_experience >= threshold:
            level = index + 1
        else:
            break
    return max(leveling_config.starting_level, min(level, leveling_config.max_level))


def progress_for_experience(
    experience: Any,
    *,
    level: Any | None = None,
    config_obj: Any | None = None,
) -> LevelProgress:
    leveling_config = get_leveling_config(config_obj)
    try:
        total_experience = int(experience or 0)
    except (TypeError, ValueError):
        total_experience = 0

    if level is None:
        normalized_level = level_for_experience(total_experience, config_obj)
    else:
        normalized_level = max(1, min(int(level or 1), leveling_config.max_level))

    current_threshold = leveling_config.leveling_curve[normalized_level - 1]
    if normalized_level >= leveling_config.max_level:
        return LevelProgress(
            level=normalized_level,
            experience=total_experience,
            experience_progress=max(0, total_experience - current_threshold),
            experience_needed=0,
            max_level=leveling_config.max_level,
        )

    next_threshold = leveling_config.leveling_curve[normalized_level]
    return LevelProgress(
        level=normalized_level,
        experience=total_experience,
        experience_progress=max(0, total_experience - current_threshold),
        experience_needed=max(0, next_threshold - current_threshold),
        max_level=leveling_config.max_level,
    )


def apply_experience(player: Any, amount: Any) -> ExperienceGrant:
    try:
        experience_gained = int(amount or 0)
    except (TypeError, ValueError):
        experience_gained = 0
    if experience_gained < 0:
        experience_gained = 0

    config_obj = get_world_leveling_config(getattr(player, "world", None))
    previous_experience = int(getattr(player, "experience", 0) or 0)
    previous_level = int(getattr(player, "level", 1) or 1)
    new_experience = previous_experience + experience_gained
    derived_level = level_for_experience(new_experience, config_obj)
    new_level = max(
        min(previous_level, config_obj.max_level),
        config_obj.starting_level,
        derived_level,
    )
    progress = progress_for_experience(
        new_experience,
        level=new_level,
        config_obj=config_obj,
    )

    player.experience = new_experience
    player.level = new_level

    return ExperienceGrant(
        experience_gained=experience_gained,
        previous_experience=previous_experience,
        new_experience=new_experience,
        previous_level=previous_level,
        new_level=new_level,
        experience_progress=progress.experience_progress,
        experience_needed=progress.experience_needed,
        max_level=progress.max_level,
    )


def set_player_level(
    player: Any,
    level: Any,
    *,
    reset_resources: bool = True,
) -> ExperienceGrant:
    config_obj = get_world_leveling_config(getattr(player, "world", None))
    new_level = clamp_level(level, config_obj)
    previous_experience = int(getattr(player, "experience", 0) or 0)
    previous_level = int(getattr(player, "level", 1) or 1)
    new_experience = experience_for_level(new_level, config_obj)

    player.level = new_level
    player.experience = new_experience

    if reset_resources:
        stats = compute_stats(
            new_level,
            getattr(player, "archetype", None),
            char=player,
            world=getattr(player, "world", None),
        )
        player.health = max(1, int(stats.get("health_max") or 1))
        player.energy = int(stats.get("energy_max") or 0)
        player.stamina = int(stats.get("stamina_max") or 0)

    progress = progress_for_experience(
        new_experience,
        level=new_level,
        config_obj=config_obj,
    )
    return ExperienceGrant(
        experience_gained=max(0, new_experience - previous_experience),
        previous_experience=previous_experience,
        new_experience=new_experience,
        previous_level=previous_level,
        new_level=new_level,
        experience_progress=progress.experience_progress,
        experience_needed=progress.experience_needed,
        max_level=progress.max_level,
    )
