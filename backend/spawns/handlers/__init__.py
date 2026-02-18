"""
Game command handlers package.

This package implements a scalable command dispatch system following the
Command → Action → Event pattern from WR2 architecture.

Usage:
    from spawns.handlers import dispatch_command

    # In a Celery task or elsewhere:
    dispatch_command("state.sync", player_id=123, payload={})

Adding new handlers:
    1. Create a new file in spawns/handlers/ (e.g., movement.py)
    2. Import and use the register_handler decorator:

        from spawns.handlers.base import CommandHandler, CommandContext
        from spawns.handlers.registry import register_handler

        @register_handler
        class NorthHandler(CommandHandler):
            command_type = "north"

            def handle(self, ctx: CommandContext) -> None:
                # Implementation here
                ctx.publish_success("north", {"room": new_room_data})

    3. Import your module in this __init__.py file

Handler organization:
    - Group related commands in the same file (e.g., all movement in movement.py)
    - Use clear, descriptive file names
    - One handler class per command type
"""
from spawns.handlers.base import CommandHandler, CommandContext
from spawns.handlers.registry import (
    ActorNotFoundError,
    dispatch_command,
    get_handler,
    get_registered_commands,
    get_registered_handlers,
    iter_text_handlers,
    register_handler,
    resolve_text_handler,
    HandlerNotFoundError,
    PlayerNotFoundError,
)

# Import handler modules to trigger registration.
# Add new handler modules here as they are created.
from spawns.handlers import state_sync
from spawns.handlers import text
from spawns.handlers import information
from spawns.handlers import movement
from spawns.handlers import communication
from spawns.handlers import builder
from spawns.handlers import items

__all__ = [
    # Base classes
    "CommandHandler",
    "CommandContext",
    # Registry functions
    "dispatch_command",
    "get_handler",
    "get_registered_commands",
    "get_registered_handlers",
    "iter_text_handlers",
    "register_handler",
    "resolve_text_handler",
    # Exceptions
    "ActorNotFoundError",
    "HandlerNotFoundError",
    "PlayerNotFoundError",
]
