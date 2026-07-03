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
    "combat_resolution_interval",
    "combat_system",
    "default_roam_chance",
    "equipment_system",
    "is_narrative",
    "leveling_curve",
    "max_level",
    "player_creation",
    "starting_level",
    "stat_system",
}

INSTANCE_LOCAL_CONFIG_FIELDS = {
    "allow_pvp",
    "built_by",
    "death_gold_penalty",
    "death_mode",
    "death_room",
    "death_route",
    "exits_to",
    "large_background",
    "pvp_mode",
    "small_background",
    "starting_room",
}

INSTANCE_INHERITED_MANIFEST_FIELDS = {
    "ability_progression",
    "allow_combat",
    "combat",
    "combat_resolution_interval",
    "default_roam_chance",
    "equipment",
    "is_narrative",
    "leveling_curve",
    "max_level",
    "player_creation",
    "starting_level",
    "stats",
}

INSTANCE_LOCAL_MANIFEST_FIELDS = {
    "allow_pvp",
    "built_by",
    "death_gold_penalty",
    "death_mode",
    "death_room",
    "death_route",
    "description",
    "is_public",
    "large_background",
    "motd",
    "name",
    "pvp_mode",
    "short_description",
    "small_background",
    "starting_room",
}


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
