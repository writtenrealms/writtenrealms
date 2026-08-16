"""Player and privileged doorway command handlers."""

from spawns.actions.base import ActionError
from spawns.actions.doors import (
    execute_forced_door_command,
    execute_player_door_command,
)
from spawns.events import publish_events
from spawns.handlers.base import (
    TRIGGER_STEP_MODE_TRANSACTIONAL,
    CommandContext,
    CommandHandler,
)
from spawns.handlers.permissions import has_builder_access
from spawns.handlers.registry import register_handler
from spawns.triggers import command_trigger_result_message


def _selector(ctx: CommandContext):
    for key in ("target", "door", "direction"):
        value = ctx.payload.get(key)
        if value not in (None, ""):
            return value
    return ctx.payload.get("args") or []


_DIRECTION_SELECTORS = {
    "n",
    "north",
    "e",
    "east",
    "s",
    "south",
    "w",
    "west",
    "u",
    "up",
    "d",
    "down",
}


def _forced_selector_and_room_message(
    ctx: CommandContext,
    command: str,
):
    """Split an optional forced-door observer message without guessing names."""
    selector = _selector(ctx)
    if command not in {"/open", "/close", "/lock"}:
        return selector, None, False

    explicit_message = str(ctx.payload.get("room_message") or "").strip()
    args = [str(arg) for arg in (ctx.payload.get("args") or [])]
    if explicit_message or not args:
        return selector, explicit_message or None, False

    if "--" in args:
        delimiter_index = args.index("--")
        delimited_selector = args[:delimiter_index]
        room_message = " ".join(args[delimiter_index + 1 :]).strip()
        return delimited_selector, room_message or None, False

    # Let the action resolve the full text first so an existing name such as
    # "south gate" retains its historical meaning. If it is not a door name,
    # the first direction becomes the target and the rest becomes the message.
    implicit_direction_message = (
        len(args) > 1
        and args[0].strip().lower() in _DIRECTION_SELECTORS
    )
    if implicit_direction_message:
        return args, None, True

    return selector, None, False


def _publish_error(ctx: CommandContext, command: str, err: ActionError) -> None:
    ctx.publish(
        {
            "type": f"cmd.{command}.error",
            "text": err.message,
            "data": {"error": err.message, "code": err.code, **err.data},
        }
    )


def _handle_player(ctx: CommandContext, command: str) -> None:
    try:
        result = execute_player_door_command(
            player_id=ctx.player.id,
            command=command,
            selector=_selector(ctx),
            request_id=ctx.payload.get("_request_id"),
            request_segment=ctx.payload.get("_request_segment", "r"),
        )
    except ActionError as err:
        raw_text = str(ctx.payload.get("raw_text") or "").strip()
        if (
            err.code in {"door_not_found", "door_target_required"}
            and raw_text
            and not ctx.payload.get("skip_triggers")
        ):
            # Door verbs existed in authored command triggers before becoming
            # built-ins. Claim the command only when it resolves to a door so
            # worlds can keep intentional commands such as "open cage".
            from spawns.triggers import execute_command_fallback_trigger

            fallback = execute_command_fallback_trigger(
                actor=ctx.player,
                text=raw_text,
                connection_id=ctx.connection_id,
                request_id=ctx.payload.get("_request_id"),
                request_segment=ctx.payload.get(
                    "_request_segment",
                    "r",
                ),
            )
            if fallback.handled:
                response = command_trigger_result_message(
                    fallback,
                    request_id=ctx.payload.get("_request_id"),
                    request_segment=ctx.payload.get(
                        "_request_segment",
                        "r",
                    ),
                )
                if response is not None:
                    ctx.publish(response)
                return
        _publish_error(ctx, command, err)
        return
    publish_events(
        result.events,
        actor_key=ctx.player.key,
        connection_id=ctx.connection_id,
    )


@register_handler
class OpenDoorHandler(CommandHandler):
    command_type = "open"
    text_commands = ("open",)
    help = {
        "name": "Open",
        "format": "open <direction|door name> [direction]",
        "description": (
            "Open a door. A locked door is unlocked and opened when you carry "
            "its key."
        ),
        "examples": ["open north", "open iron gate", "open gate east"],
    }

    def handle(self, ctx: CommandContext) -> None:
        _handle_player(ctx, self.command_type)


@register_handler
class CloseDoorHandler(CommandHandler):
    command_type = "close"
    text_commands = ("close",)
    help = {
        "name": "Close",
        "format": "close <direction|door name> [direction]",
        "description": "Begin closing an open door.",
        "details": ["Closing takes 2.5 seconds and is interrupted if you move."],
        "examples": ["close north", "close iron gate"],
    }

    def handle(self, ctx: CommandContext) -> None:
        _handle_player(ctx, self.command_type)


@register_handler
class LockDoorHandler(CommandHandler):
    command_type = "lock"
    text_commands = ("lock",)
    help = {
        "name": "Lock",
        "format": "lock <direction|door name> [direction]",
        "description": "Lock a door with a directly carried matching key.",
        "details": ["An open door takes 2.5 seconds to close and lock."],
        "examples": ["lock north", "lock iron gate west"],
    }

    def handle(self, ctx: CommandContext) -> None:
        _handle_player(ctx, self.command_type)


@register_handler
class UnlockDoorHandler(CommandHandler):
    command_type = "unlock"
    text_commands = ("unlock",)
    help = {
        "name": "Unlock",
        "format": "unlock <direction|door name> [direction]",
        "description": "Unlock a door with a directly carried matching key.",
        "details": ["The door remains closed. Use open to pass through it."],
        "examples": ["unlock north", "unlock iron gate"],
    }

    def handle(self, ctx: CommandContext) -> None:
        _handle_player(ctx, self.command_type)


def _trusted_forced_issuer(ctx: CommandContext) -> bool:
    if ctx.script_source:
        return ctx.actor_type in {"mob", "room"}
    return ctx.actor_type == "player" and has_builder_access(ctx.player)


def _handle_forced(ctx: CommandContext, command: str) -> None:
    if not _trusted_forced_issuer(ctx):
        err = ActionError(
            (
                "Only a builder issuing the command directly, or a trusted "
                "room/mob script, may force door state."
            ),
            code="door_issuer_not_allowed",
        )
        _publish_error(ctx, command, err)
        return
    try:
        (
            selector,
            room_message,
            implicit_direction_message,
        ) = _forced_selector_and_room_message(ctx, command)
        result = execute_forced_door_command(
            actor=ctx.actor,
            actor_type=ctx.actor_type,
            runtime_world=ctx.world,
            room=ctx.room,
            command=command,
            selector=selector,
            room_message=room_message,
            implicit_direction_message=implicit_direction_message,
        )
    except ActionError as err:
        _publish_error(ctx, command, err)
        return
    publish_events(
        result.events,
        actor_key=ctx.actor_key,
        connection_id=ctx.connection_id,
    )


class _ForcedDoorHandler(CommandHandler):
    command_type = "_forced_door"
    builder_only = True
    allow_script_source = True
    supported_actor_types = ("player", "mob", "room")

    def validate_trigger_step_command(
        self,
        *,
        command: str,
        subject_type: str,
        subject_key: str,
        render_actor_key: str,
    ) -> tuple[str, str] | None:
        del command, subject_key, render_actor_key
        if subject_type not in {"mob", "room"}:
            return (
                "Trigger-step door commands require a room or mob subject.",
                "unsupported_command_subject",
            )
        return None

    def handle(self, ctx: CommandContext) -> None:
        _handle_forced(ctx, self.command_type)


@register_handler
class ForceOpenDoorHandler(_ForcedDoorHandler):
    command_type = "/open"
    text_commands = ("/open",)
    trigger_step_mode = TRIGGER_STEP_MODE_TRANSACTIONAL
    help = {
        "name": "Force Open",
        "format": "/open <direction|door name> [direction] [-- <room message>]",
        "description": "Immediately force a door open, bypassing its key.",
        "details": [
            "A direction target can omit -- before the optional room message.",
        ],
    }


@register_handler
class ForceCloseDoorHandler(_ForcedDoorHandler):
    command_type = "/close"
    text_commands = ("/close",)
    trigger_step_mode = TRIGGER_STEP_MODE_TRANSACTIONAL
    help = {
        "name": "Force Close",
        "format": "/close <direction|door name> [direction] [-- <room message>]",
        "description": "Immediately close an open door without unlocking it.",
        "details": [
            "A direction target can omit -- before the optional room message.",
        ],
    }


@register_handler
class ForceLockDoorHandler(_ForcedDoorHandler):
    command_type = "/lock"
    text_commands = ("/lock",)
    trigger_step_mode = TRIGGER_STEP_MODE_TRANSACTIONAL
    help = {
        "name": "Force Lock",
        "format": "/lock <direction|door name> [direction] [-- <room message>]",
        "description": "Immediately force a door closed and locked.",
        "details": [
            "A direction target can omit -- before the optional room message.",
        ],
    }


@register_handler
class ForceUnlockDoorHandler(_ForcedDoorHandler):
    command_type = "/unlock"
    text_commands = ("/unlock",)
    help = {
        "name": "Force Unlock",
        "format": "/unlock <direction|door name> [direction]",
        "description": "Immediately force a locked door closed and unlocked.",
    }
