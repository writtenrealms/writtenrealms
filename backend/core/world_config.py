"""
Helpers for resolving world configuration scopes.

Spawn worlds use their template world's config for local runtime settings. Instance
templates also need local config for rooms and penalties, but authored systems
such as stats, equipment, combat formulas, leveling, and ability progression are
inherited from the base world.
"""

from typing import Any


INSTANCE_INHERITED_CONFIG_FIELDS = {
    "ability_progression",
    "allow_combat",
    "announce_duel_results",
    "clan_registration_cost",
    "clan_registration_currency",
    "combat_resolution_interval",
    "combat_system",
    "default_roam_chance",
    "equipment_system",
    "flee_to_unknown_rooms",
    "is_narrative",
    "leveling_curve",
    "max_level",
    "player_creation",
    "starting_equipment",
    "starting_level",
    "stat_system",
}

INSTANCE_LOCAL_CONFIG_FIELDS = {
    "instance_single_player",
    "instance_time_control",
    "instance_goal",
    "never_reload",
    "cross_race_cooldown",
    "built_by",
    "death_currency",
    "death_currency_penalty",
    "death_mode",
    "death_room",
    "death_route",
    "death_routing",
    "death_routing_source",
    "exits_to",
    "large_background",
    "pvp_mode",
    "small_background",
    "starting_room",
}

INSTANCE_INHERITED_MANIFEST_FIELDS = {
    "ability_progression",
    "allow_combat",
    "announce_duel_results",
    "clan_registration_cost",
    "clan_registration_currency",
    "combat",
    "combat_resolution_interval",
    "default_roam_chance",
    "equipment",
    "flee_to_unknown_rooms",
    "is_narrative",
    "leveling_curve",
    "max_level",
    "player_creation",
    "starting_equipment",
    "starting_level",
    "stats",
}

INSTANCE_LOCAL_MANIFEST_FIELDS = {
    "instance_single_player",
    "instance_time_control",
    "instance_goal",
    "never_reload",
    "cross_race_cooldown",
    "built_by",
    "death_currency",
    "death_currency_penalty",
    "death_mode",
    "death_room",
    "death_route",
    "death_routing",
    "death_routing_source",
    "description",
    "is_public",
    "initial_state",
    "large_background",
    "motd",
    "name",
    "pvp_mode",
    "short_description",
    "small_background",
    "starting_room",
}


def validate_instance_control_config(*, world, config, updates):
    """Validate partial authoring updates against the effective local policy."""
    single_player = updates.get(
        "instance_single_player", getattr(config, "instance_single_player", False)
    )
    time_control = updates.get(
        "instance_time_control", getattr(config, "instance_time_control", False)
    )
    if (single_player or time_control) and (
        not getattr(world, "instance_of_id", None)
        or getattr(world, "context_id", None)
    ):
        raise ValueError(
            "Single-player and time control are only configurable for instance templates."
        )
    if time_control and not single_player:
        raise ValueError("Instance time control requires a single-player instance.")
    from worlds.instance_goals import normalize_instance_goal
    goal = normalize_instance_goal(updates.get('instance_goal', getattr(config, 'instance_goal', {})))
    if goal and (not getattr(world, 'instance_of_id', None) or getattr(world, 'context_id', None)):
        raise ValueError('Instance goals are only configurable for instance templates.')
    if goal and updates.get('pvp_mode', getattr(config, 'pvp_mode', None)) == 'match':
        raise ValueError('Instance goals cannot be combined with PvP match mode.')
    if single_player and updates.get('pvp_mode', getattr(config, 'pvp_mode', None)) == 'match':
        raise ValueError('Single-player instances cannot use PvP match mode.')


def inherited_system_world(world: Any | None) -> Any | None:
    if world is None:
        return None

    context = getattr(world, "context", None)
    if context is not None:
        instance_base = getattr(context, "instance_of", None)
        if instance_base is not None:
            return instance_base
        return context

    instance_base = getattr(world, "instance_of", None)
    if instance_base is not None:
        return instance_base

    return world


def inherited_system_config(world: Any | None) -> Any | None:
    source_world = inherited_system_world(world)
    if source_world is None:
        return None
    return getattr(source_world, "config", None)
