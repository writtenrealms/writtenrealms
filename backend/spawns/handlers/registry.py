"""
Handler registry and dispatch system.

Provides decorator-based handler registration and a central dispatch function.
Handlers are automatically discovered when their modules are imported.
"""
from typing import Type

from spawns.handlers.base import CommandHandler, CommandContext
from spawns.models import Mob, Player


class HandlerNotFoundError(Exception):
    """Raised when no handler is registered for a command type."""
    def __init__(self, command_type: str):
        self.command_type = command_type
        super().__init__(f"No handler registered for command: {command_type}")


class ActorNotFoundError(Exception):
    """Raised when an actor cannot be resolved."""
    def __init__(self, actor_type: str, actor_id: int):
        self.actor_type = actor_type
        self.actor_id = actor_id
        super().__init__(f"{actor_type.capitalize()} not found: {actor_id}")


class PlayerNotFoundError(ActorNotFoundError):
    """Raised when the player cannot be resolved."""
    def __init__(self, player_id: int):
        self.player_id = player_id
        super().__init__("player", player_id)


# Global handler registry: command_type -> handler instance
_handlers: dict[str, CommandHandler] = {}


def register_handler(cls: Type[CommandHandler]) -> Type[CommandHandler]:
    """
    Decorator to register a command handler.

    Usage:
        @register_handler
        class MyHandler(CommandHandler):
            command_type = "my.command"

            def handle(self, ctx: CommandContext) -> None:
                ...

    Handlers are instantiated once at registration time (singleton pattern).
    """
    if not issubclass(cls, CommandHandler):
        raise TypeError(f"{cls.__name__} must inherit from CommandHandler")

    command_type = cls.command_type
    if command_type in _handlers:
        raise ValueError(
            f"Duplicate handler registration for '{command_type}': "
            f"{cls.__name__} conflicts with {_handlers[command_type].__class__.__name__}"
        )

    _handlers[command_type] = cls()
    return cls


def get_handler(command_type: str) -> CommandHandler:
    """Get the registered handler for a command type."""
    handler = _handlers.get(command_type)
    if handler is None:
        raise HandlerNotFoundError(command_type)
    return handler


def get_registered_commands() -> list[str]:
    """Return a list of all registered command types."""
    return sorted(_handlers.keys())


def get_registered_handlers() -> dict[str, CommandHandler]:
    """Return a copy of the handler registry."""
    return dict(_handlers)


def iter_text_handlers(include_builder: bool = True) -> list[tuple[str, CommandHandler]]:
    """
    Return (text_command, handler) pairs in registration order.

    Command resolution relies on this order to resolve ambiguous prefixes.
    """
    routes: list[tuple[str, CommandHandler]] = []
    for handler in _handlers.values():
        text_commands = getattr(handler, "text_commands", ()) or ()
        if not text_commands:
            continue
        if getattr(handler, "builder_only", False) and not include_builder:
            continue
        for text_command in text_commands:
            routes.append((text_command, handler))
    return routes


def resolve_text_handler(
    command: str,
    *,
    include_builder: bool = True,
) -> tuple[str, CommandHandler] | None:
    """
    Resolve a raw text command (including partials) to a handler route.
    """
    command = command.lower()
    for text_command, handler in iter_text_handlers(include_builder=include_builder):
        if text_command.startswith(command):
            return text_command, handler
    return None


def dispatch_command(
    command_type: str,
    payload: dict,
    player_id: int | None = None,
    connection_id: str | None = None,
    *,
    actor_type: str | None = None,
    actor_id: int | None = None,
    published_messages: list[dict] | None = None,
) -> None:
    """
    Dispatch a command to its registered handler.

    This is the main entry point for command processing. It:
    1. Resolves the actor from actor_type/actor_id (or player_id fallback)
    2. Looks up the handler for command_type
    3. Builds a CommandContext
    4. Invokes the handler

    Args:
        command_type: The command to execute (e.g., "state.sync", "look")
        player_id: Backwards-compatible player ID.
        actor_type: "player" or "mob". Defaults to "player" when player_id is provided.
        actor_id: Actor database ID.
        payload: Command-specific data from the client
        connection_id: Optional WebSocket connection identifier

    Raises:
        ActorNotFoundError: If the actor cannot be resolved
        HandlerNotFoundError: If no handler is registered for command_type
    """
    resolved_actor_type = actor_type
    resolved_actor_id = actor_id

    if resolved_actor_type is None and player_id is not None:
        resolved_actor_type = "player"
    if resolved_actor_id is None and resolved_actor_type == "player" and player_id is not None:
        resolved_actor_id = player_id

    if not resolved_actor_type or resolved_actor_id is None:
        raise ValueError("dispatch_command requires actor_type and actor_id (or player_id).")

    # Resolve actor
    actor = None
    player = None
    mob = None
    if resolved_actor_type == "player":
        try:
            player = Player.objects.get(pk=resolved_actor_id)
        except Player.DoesNotExist:
            raise PlayerNotFoundError(resolved_actor_id)
        actor = player
    elif resolved_actor_type == "mob":
        try:
            mob = Mob.objects.get(pk=resolved_actor_id)
        except Mob.DoesNotExist:
            raise ActorNotFoundError("mob", resolved_actor_id)
        actor = mob
    else:
        raise ValueError(f"Unsupported actor_type: {resolved_actor_type}")

    # Get handler
    handler = get_handler(command_type)

    # Build context
    actor_key = actor.key
    ctx = CommandContext(
        actor=actor,
        actor_type=resolved_actor_type,
        actor_id=resolved_actor_id,
        actor_key=actor_key,
        payload=payload,
        connection_id=connection_id,
        player=player,
        mob=mob,
        published_messages=published_messages,
    )

    # Guard direct dispatches that target unsupported actor types.
    if resolved_actor_type not in getattr(handler, "supported_actor_types", ("player",)):
        ctx.publish(
            {
                "type": f"cmd.{command_type}.error",
                "text": f"{resolved_actor_type.capitalize()}s cannot execute {command_type}.",
                "data": {
                    "error": f"Unsupported actor type: {resolved_actor_type}.",
                    "code": "unsupported_actor",
                },
            }
        )
        return

    # Execute
    handler.handle(ctx)
