"""
Shared permission helpers for command handlers.
"""
from worlds.models import World

from spawns.models import Player


BUILDER_PERMISSION_TEXT = "You do not have permission to use builder commands."


def builder_context_world(world: World) -> World:
    if world.context:
        if world.context.instance_of:
            return world.context.instance_of
        return world.context
    if world.instance_of:
        return world.instance_of
    return world


def has_builder_access(player: Player | None) -> bool:
    """Return whether this player character may issue builder commands."""
    if not player or not getattr(player, "is_builder", False):
        return False
    user = getattr(player, "user", None)
    if not user:
        return False
    builder_world = builder_context_world(player.world)
    return builder_world.can_edit(user)


def can_execute_builder_command(ctx, handler) -> bool:
    if has_builder_access(getattr(ctx, "player", None)):
        return True
    if (
        getattr(ctx, "actor_type", None) == "mob"
        and getattr(handler, "allow_mob_actor", False)
    ):
        return True
    return bool(
        getattr(ctx, "script_source", False)
        and getattr(handler, "allow_script_source", False)
    )


def builder_permission_error(command_type: str) -> dict:
    return {
        "type": f"cmd.{command_type}.error",
        "text": BUILDER_PERMISSION_TEXT,
        "data": {"error": "Builder permissions required."},
    }
