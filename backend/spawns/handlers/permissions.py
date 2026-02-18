"""
Shared permission helpers for command handlers.
"""
from worlds.models import World

from spawns.models import Player


def builder_context_world(world: World) -> World:
    if world.context:
        if world.context.instance_of:
            return world.context.instance_of
        return world.context
    if world.instance_of:
        return world.instance_of
    return world


def has_builder_access(player: Player) -> bool:
    user = getattr(player, "user", None)
    if not user:
        return False
    builder_world = builder_context_world(player.world)
    return builder_world.can_edit(user)
