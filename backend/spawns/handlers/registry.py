"""
Handler registry and dispatch system.

Provides decorator-based handler registration and a central dispatch function.
Handlers are automatically discovered when their modules are imported.
"""
from typing import Type

from spawns.events import (
    command_request_completed_message,
    command_request_scope,
)
from spawns.handlers.base import CommandActor, CommandHandler, CommandContext
from spawns.models import Mob, Player
from worlds.models import Room, World, Zone


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


def _payload_int(payload: dict, *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _resolve_runtime_world(payload: dict, default_world: World | None) -> World | None:
    world_id = _payload_int(payload, "runtime_world_id", "world_id")
    if world_id is None:
        return default_world
    try:
        return World.objects.get(pk=world_id)
    except World.DoesNotExist:
        raise ActorNotFoundError("world", world_id)


def _resolve_command_actor(
    *,
    actor_type: str,
    actor_id: int,
    payload: dict,
) -> tuple[
    CommandActor,
    Player | None,
    Mob | None,
    Room | None,
    Zone | None,
    World | None,
]:
    player = None
    mob = None
    room = None
    zone = None
    world = None
    if actor_type == "player":
        try:
            player = Player.objects.get(pk=actor_id)
        except Player.DoesNotExist:
            raise PlayerNotFoundError(actor_id)
        actor = player
        world = player.world
        room = player.room
        zone = getattr(room, "zone", None)
    elif actor_type == "mob":
        try:
            mob = Mob.objects.get(pk=actor_id)
        except Mob.DoesNotExist:
            raise ActorNotFoundError("mob", actor_id)
        actor = mob
        world = mob.world
        room = mob.room
        zone = getattr(room, "zone", None)
    elif actor_type == "room":
        try:
            room = Room.objects.select_related("world", "zone").get(pk=actor_id)
        except Room.DoesNotExist:
            raise ActorNotFoundError("room", actor_id)
        actor = room
        world = _resolve_runtime_world(payload, room.world)
        zone = room.zone
    elif actor_type == "zone":
        try:
            zone = Zone.objects.select_related("world", "center").get(pk=actor_id)
        except Zone.DoesNotExist:
            raise ActorNotFoundError("zone", actor_id)
        actor = zone
        world = _resolve_runtime_world(payload, zone.world)
        room = zone.center
    elif actor_type == "world":
        try:
            world = World.objects.get(pk=actor_id)
        except World.DoesNotExist:
            raise ActorNotFoundError("world", actor_id)
        actor = world
    else:
        raise ValueError(f"Unsupported actor_type: {actor_type}")
    return actor, player, mob, room, zone, world


def _resolved_command_actor_context(
    *,
    actor_type: str,
    actor_id: int,
    actor: CommandActor,
    payload: dict,
    runtime_world: World | None = None,
    room_hint: Room | None = None,
) -> tuple[
    CommandActor,
    Player | None,
    Mob | None,
    Room | None,
    Zone | None,
    World | None,
]:
    actor_models = {
        "player": Player,
        "mob": Mob,
        "room": Room,
        "zone": Zone,
        "world": World,
    }
    actor_model = actor_models.get(actor_type)
    if actor_model is None:
        raise ValueError(f"Unsupported actor_type: {actor_type}")
    if not isinstance(actor, actor_model) or actor.pk != actor_id:
        raise ValueError(
            "The resolved command actor does not match its declared identity."
        )

    payload_world_id = _payload_int(payload, "runtime_world_id", "world_id")
    if (
        runtime_world is not None
        and payload_world_id is not None
        and runtime_world.id != payload_world_id
    ):
        raise ValueError(
            "The resolved runtime world does not match the command payload."
        )

    player = actor if isinstance(actor, Player) else None
    mob = actor if isinstance(actor, Mob) else None
    room = actor if isinstance(actor, Room) else None
    zone = actor if isinstance(actor, Zone) else None
    world = actor if isinstance(actor, World) else None

    if player is not None or mob is not None:
        embodied = player or mob
        world = runtime_world or embodied.world
        if world.id != embodied.world_id:
            raise ValueError(
                "The resolved command actor is outside the runtime world."
            )
        if room_hint is not None and room_hint.id == embodied.room_id:
            room = room_hint
        else:
            room = embodied.room
        zone = room.zone if room is not None else None
    elif room is not None:
        world = runtime_world or _resolve_runtime_world(payload, room.world)
        zone = room.zone
    elif zone is not None:
        world = runtime_world or _resolve_runtime_world(payload, zone.world)
        room = zone.center
    elif world is not None and runtime_world is not None:
        if world.id != runtime_world.id:
            raise ValueError(
                "The resolved world actor does not match the runtime world."
            )
        world = runtime_world

    return actor, player, mob, room, zone, world


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
    routes = iter_text_handlers(include_builder=include_builder)
    for text_command, handler in routes:
        if text_command == command:
            return text_command, handler

    for _text_command, handler in routes:
        aliases = getattr(handler, "text_aliases", {}) or {}
        for alias, target in aliases.items():
            alias = str(alias).strip().lower()
            target = str(target).strip().lower()
            if alias == command and target:
                return target, handler

    for text_command, handler in routes:
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
    script_source: bool = False,
    capture_only: bool = False,
    trigger_step: bool = False,
    builder_force: bool = False,
    issuer_type: str | None = None,
    issuer_id: int | None = None,
    subject_type: str | None = None,
    subject_id: int | None = None,
    resolved_actor: CommandActor | None = None,
    resolved_issuer: CommandActor | None = None,
    resolved_runtime_world: World | None = None,
) -> None:
    """
    Dispatch a command to its registered handler.

    This is the main entry point for command processing. It:
    1. Resolves the compatibility actor plus explicit issuer/subject identity
    2. Looks up the handler for command_type
    3. Builds a CommandContext
    4. Invokes the handler

    Args:
        command_type: The command to execute (e.g., "state.sync", "look")
        player_id: Backwards-compatible player ID.
        actor_type: "player", "mob", "room", "zone", or "world".
            Defaults to "player" when player_id is provided.
        actor_id: Actor database ID.
        issuer_type/issuer_id: Optional originator of the command intent.
        subject_type/subject_id: Optional embodied execution subject. When
            present, this is also the compatibility actor for existing
            handlers.
        resolved_actor/resolved_issuer/resolved_runtime_world: Optional
            already-resolved internal context. Identity fields remain
            authoritative and are validated before these objects are reused.
        payload: Command-specific data from the client
        connection_id: Optional WebSocket connection identifier

    Raises:
        ActorNotFoundError: If the actor cannot be resolved
        HandlerNotFoundError: If no handler is registered for command_type
    """
    explicit_subject = subject_type is not None or subject_id is not None
    if explicit_subject and (subject_type is None or subject_id is None):
        raise ValueError(
            "dispatch_command requires both subject_type and subject_id."
        )
    explicit_issuer = issuer_type is not None or issuer_id is not None
    if explicit_issuer and (issuer_type is None or issuer_id is None):
        raise ValueError(
            "dispatch_command requires both issuer_type and issuer_id."
        )

    resolved_actor_type = subject_type or actor_type
    resolved_actor_id = subject_id if explicit_subject else actor_id

    if resolved_actor_type is None and player_id is not None:
        resolved_actor_type = "player"
    if resolved_actor_id is None and resolved_actor_type == "player" and player_id is not None:
        resolved_actor_id = player_id

    if not resolved_actor_type or resolved_actor_id is None:
        if issuer_type and issuer_id is not None:
            resolved_actor_type = issuer_type
            resolved_actor_id = issuer_id
        else:
            raise ValueError(
                "dispatch_command requires actor_type and actor_id "
                "(or player_id)."
            )

    if actor_type is not None and explicit_subject and actor_type != subject_type:
        raise ValueError("actor_type and subject_type must match when both are set.")
    if actor_id is not None and explicit_subject and actor_id != subject_id:
        raise ValueError("actor_id and subject_id must match when both are set.")

    resolved_issuer_type = issuer_type or resolved_actor_type
    resolved_issuer_id = (
        issuer_id
        if issuer_id is not None
        else resolved_actor_id
    )

    room_hint = (
        resolved_issuer
        if isinstance(resolved_issuer, Room)
        else None
    )
    if resolved_actor is None:
        actor, player, mob, room, zone, world = _resolve_command_actor(
            actor_type=resolved_actor_type,
            actor_id=resolved_actor_id,
            payload=payload,
        )
    else:
        actor, player, mob, room, zone, world = (
            _resolved_command_actor_context(
                actor_type=resolved_actor_type,
                actor_id=resolved_actor_id,
                actor=resolved_actor,
                payload=payload,
                runtime_world=resolved_runtime_world,
                room_hint=room_hint,
            )
        )

    if (
        resolved_issuer_type == resolved_actor_type
        and resolved_issuer_id == resolved_actor_id
    ):
        if (
            resolved_issuer is not None
            and resolved_issuer is not actor
        ):
            raise ValueError(
                "The resolved command issuer conflicts with the command actor."
            )
        issuer = actor
    elif resolved_issuer is not None:
        issuer, *_issuer_context = _resolved_command_actor_context(
            actor_type=resolved_issuer_type,
            actor_id=resolved_issuer_id,
            actor=resolved_issuer,
            payload=payload,
            runtime_world=resolved_runtime_world,
        )
    else:
        issuer, *_issuer_context = _resolve_command_actor(
            actor_type=resolved_issuer_type,
            actor_id=resolved_issuer_id,
            payload=payload,
        )

    subject = actor if resolved_actor_type in {"player", "mob"} else None

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
        room=room,
        zone=zone,
        world=world,
        published_messages=published_messages,
        script_source=script_source,
        capture_only=capture_only,
        trigger_step=trigger_step,
        builder_force=builder_force,
        issuer=issuer,
        issuer_type=resolved_issuer_type,
        issuer_id=resolved_issuer_id,
        issuer_key=issuer.key,
        subject=subject,
        subject_type=resolved_actor_type if subject is not None else None,
        subject_id=resolved_actor_id if subject is not None else None,
        subject_key=actor.key if subject is not None else None,
    )

    with command_request_scope(
        request_id=payload.get("_request_id"),
        request_segment=payload.get("_request_segment"),
        actor_key=actor_key,
        enabled=(
            resolved_actor_type == "player"
            and not script_source
        ),
    ) as request_scope:
        # Guard direct dispatches that target unsupported actor types.
        if resolved_actor_type not in getattr(
            handler,
            "supported_actor_types",
            ("player",),
        ):
            ctx.publish(
                {
                    "type": f"cmd.{command_type}.error",
                    "text": (
                        f"{resolved_actor_type.capitalize()}s cannot execute "
                        f"{command_type}."
                    ),
                    "data": {
                        "error": (
                            f"Unsupported actor type: "
                            f"{resolved_actor_type}."
                        ),
                        "code": "unsupported_actor",
                    },
                }
            )
        elif getattr(handler, "builder_only", False):
            from spawns.handlers.permissions import (
                builder_permission_error,
                can_execute_builder_command,
            )

            if not can_execute_builder_command(ctx, handler):
                ctx.publish(builder_permission_error(command_type))
            else:
                handler.handle(ctx)
        else:
            handler.handle(ctx)

        if request_scope.needs_completion_event:
            ctx.publish(
                command_request_completed_message(request_scope.scope)
            )
