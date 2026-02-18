"""
Builder command handlers.

Builder commands start with "/" and require world builder or author access.
"""
from spawns.actions.base import ActionError
from spawns.actions.builder import (
    CmdAction,
    EchoAction,
    JumpAction,
    LoadTemplateAction,
    PurgeAction,
    ResyncItemTemplatesAction,
    ResyncMobTemplatesAction,
)
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.permissions import has_builder_access
from spawns.handlers.registry import register_handler

SCOPED_ECHO_ALIASES = {
    "/wecho": "world",
    "/zecho": "zone",
}
SCOPED_CMD_ALIASES = {
    "/rcmd": "room",
    "/wcmd": "world",
    "/zcmd": "zone",
}


def _is_trigger_source(ctx: CommandContext) -> bool:
    return bool(ctx.payload.get("__trigger_source"))


def _split_delimited_args(args: list[str]) -> tuple[str, str] | tuple[None, None]:
    if "--" not in args:
        return None, None
    delimiter_idx = args.index("--")
    lhs = " ".join(args[:delimiter_idx]).strip()
    rhs = " ".join(args[delimiter_idx + 1 :]).strip()
    return lhs, rhs


def _parse_echo_scope_and_message(ctx: CommandContext) -> tuple[str | None, str | None]:
    command_name = str(ctx.payload.get("command", "")).lower()
    args = list(ctx.payload.get("args", []))

    if command_name in SCOPED_ECHO_ALIASES:
        _, message = _split_delimited_args(args)
        if message:
            return SCOPED_ECHO_ALIASES[command_name], message
        fallback_message = " ".join(args).strip()
        if fallback_message.startswith("--"):
            fallback_message = fallback_message[2:].strip()
        return SCOPED_ECHO_ALIASES[command_name], fallback_message or None

    # Optional delimiter form for nested or scripted usage:
    # /echo [scope] -- <message>
    scope_token, message = _split_delimited_args(args)
    if message:
        scope = (scope_token or str(ctx.payload.get("issuer_scope") or "")).strip().lower()
        if not scope:
            scope = "room"
        return scope, message

    # Ergonomic default form:
    # /echo <message...>            -> room
    # /echo <scope> <message...>    -> explicit scope
    if not args:
        return None, None

    first = str(args[0]).strip().lower()
    if first in ("room", "zone", "world"):
        if len(args) < 2:
            return None, None
        return first, " ".join(args[1:]).strip()

    inherited_scope = str(ctx.payload.get("issuer_scope") or "").strip().lower()
    return inherited_scope or "room", " ".join(args).strip()


def _parse_cmd_target_and_command(ctx: CommandContext) -> tuple[str | None, str | None]:
    command_name = str(ctx.payload.get("command", "")).lower()
    args = list(ctx.payload.get("args", []))
    target, nested_command = _split_delimited_args(args)

    if command_name in SCOPED_CMD_ALIASES:
        return SCOPED_CMD_ALIASES[command_name], nested_command

    if command_name == "/force" and nested_command is None:
        if len(args) < 2:
            return None, None
        return args[0], " ".join(args[1:]).strip()

    return target, nested_command


@register_handler
class LoadHandler(CommandHandler):
    command_type = "/load"
    text_commands = ("/load",)
    builder_only = True
    help = {
        "name": "Load",
        "format": "/load <item|mob> <template_id> [cmd]",
        "description": (
            "Load an item or mob template into your current room. "
            "An optional trailing command is attached to the loaded entity."
        ),
        "examples": [
            "/load item 123",
            "/load mob 456",
            "/load mob 456 say Hello there!",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not has_builder_access(ctx.player):
            ctx.publish(
                {
                    "type": "cmd./load.error",
                    "text": "You do not have permission to use builder commands.",
                    "data": {"error": "Builder permissions required."},
                }
            )
            return

        template_type = ctx.payload.get("template_type")
        template_id = ctx.payload.get("template_id")
        cmd = ctx.payload.get("cmd")

        if not template_type or not template_id:
            args = ctx.payload.get("args", [])
            if len(args) < 2:
                ctx.publish(
                    {
                        "type": "cmd./load.error",
                        "text": "Usage: /load <item|mob> <template_id> [cmd]",
                        "data": {"error": "Missing arguments.", "code": "invalid_args"},
                    }
                )
                return
            template_type = args[0]
            template_id = args[1]
            if len(args) > 2:
                cmd = " ".join(args[2:])

        template_type = str(template_type).lower()
        try:
            template_id_int = int(template_id)
        except (TypeError, ValueError):
            ctx.publish(
                {
                    "type": "cmd./load.error",
                    "text": "Template ID must be a number.",
                    "data": {"error": "Invalid template ID.", "code": "invalid_id"},
                }
            )
            return

        if template_type not in ("item", "mob"):
            ctx.publish(
                {
                    "type": "cmd./load.error",
                    "text": "Template type must be item or mob.",
                    "data": {"error": "Invalid template type.", "code": "invalid_type"},
                }
            )
            return

        try:
            result = LoadTemplateAction().execute(
                player_id=ctx.player.id,
                template_type=template_type,
                template_id=template_id_int,
                cmd=cmd,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./load.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class PurgeHandler(CommandHandler):
    command_type = "/purge"
    text_commands = ("/purge",)
    builder_only = True
    help = {
        "name": "Purge",
        "format": "/purge | /purge <target>",
        "description": "Delete mobs and items from the current room, or purge a specific target.",
        "examples": [
            "/purge",
            "/purge soldier",
            "/purge mobs",
            "/purge items",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not has_builder_access(ctx.player):
            ctx.publish(
                {
                    "type": "cmd./purge.error",
                    "text": "You do not have permission to use builder commands.",
                    "data": {"error": "Builder permissions required."},
                }
            )
            return

        target = ctx.payload.get("target")
        if not target:
            args = ctx.payload.get("args", [])
            if args:
                target = " ".join(args).strip()

        try:
            result = PurgeAction().execute(
                player_id=ctx.player.id,
                target=target,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./purge.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class EchoHandler(CommandHandler):
    command_type = "/echo"
    text_commands = ("/echo", "/zecho", "/wecho")
    builder_only = True
    supported_actor_types = ("player", "mob")
    help = {
        "name": "Echo",
        "format": "/echo [room|zone|world] <message>",
        "description": (
            "Broadcast a message to room, zone, or world. "
            "Aliases: /zecho and /wecho."
        ),
        "examples": [
            "/echo A cold breeze passes through.",
            "/echo zone The bells ring in the distance.",
            "/echo room -- The torches flicker.",
            "/wecho The world trembles.",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if (
            ctx.actor_type == "player"
            and not has_builder_access(ctx.player)
            and not _is_trigger_source(ctx)
        ):
            ctx.publish(
                {
                    "type": "cmd./echo.error",
                    "text": "You do not have permission to use builder commands.",
                    "data": {"error": "Builder permissions required."},
                }
            )
            return

        scope, message = _parse_echo_scope_and_message(ctx)
        if not scope or not message:
            ctx.publish(
                {
                    "type": "cmd./echo.error",
                    "text": "Usage: /echo [room|zone|world] <message>",
                    "data": {"error": "Missing scope or message.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = EchoAction().execute(
                actor=ctx.actor,
                scope=scope,
                message=message,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./echo.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )


@register_handler
class CmdHandler(CommandHandler):
    command_type = "/cmd"
    text_commands = ("/cmd", "/force", "/rcmd", "/zcmd", "/wcmd")
    builder_only = True
    supported_actor_types = ("player", "mob")
    help = {
        "name": "Cmd",
        "format": "/cmd <room|zone|world|target> -- <command>",
        "description": (
            "Run a command either as room/zone/world context or as a targeted mob. "
            "Use && to chain commands. /force is kept as an alias."
        ),
        "examples": [
            "/cmd mob:guard -- say Halt!",
            "/cmd room -- /echo room -- The torch sputters.",
            "/zcmd -- /echo -- The zone grows quiet.",
            "/force guard -- emote salutes.",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if (
            ctx.actor_type == "player"
            and not has_builder_access(ctx.player)
            and not _is_trigger_source(ctx)
        ):
            ctx.publish(
                {
                    "type": "cmd./cmd.error",
                    "text": "You do not have permission to use builder commands.",
                    "data": {"error": "Builder permissions required."},
                }
            )
            return

        target_selector, cmd = _parse_cmd_target_and_command(ctx)
        if not target_selector or not cmd:
            ctx.publish(
                {
                    "type": "cmd./cmd.error",
                    "text": "Usage: /cmd <room|zone|world|target> -- <command>",
                    "data": {"error": "Missing target or command.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = CmdAction().execute(
                actor=ctx.actor,
                target_selector=target_selector,
                cmd=cmd,
                skip_triggers=bool(ctx.payload.get("skip_triggers")),
                trigger_source=_is_trigger_source(ctx),
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./cmd.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )


@register_handler
class JumpHandler(CommandHandler):
    command_type = "/jump"
    text_commands = ("/jump",)
    builder_only = True
    help = {
        "name": "Jump",
        "format": "/jump <room_id>",
        "description": "Instantly move yourself to another room by room ID.",
        "examples": [
            "/jump 50201",
            "/jump room.50201",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not has_builder_access(ctx.player):
            ctx.publish(
                {
                    "type": "cmd./jump.error",
                    "text": "You do not have permission to use builder commands.",
                    "data": {"error": "Builder permissions required."},
                }
            )
            return

        room_selector = ctx.payload.get("to")
        if not room_selector:
            args = ctx.payload.get("args", [])
            if not args:
                ctx.publish(
                    {
                        "type": "cmd./jump.error",
                        "text": "Usage: /jump <room_id>",
                        "data": {"error": "Missing room ID.", "code": "invalid_args"},
                    }
                )
                return
            room_selector = args[0]

        try:
            result = JumpAction().execute(
                player_id=ctx.player.id,
                room_selector=str(room_selector),
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./jump.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class ResyncHandler(CommandHandler):
    command_type = "/resync"
    text_commands = ("/resync",)
    builder_only = True
    help = {
        "name": "Resync",
        "format": "/resync <item|mob> <template_id|all>",
        "description": (
            "Reapply template fields to spawned item or mob instances in your current world."
        ),
        "examples": [
            "/resync item 509",
            "/resync item all",
            "/resync mob 456",
            "/resync mob all",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not has_builder_access(ctx.player):
            ctx.publish(
                {
                    "type": "cmd./resync.error",
                    "text": "You do not have permission to use builder commands.",
                    "data": {"error": "Builder permissions required."},
                }
            )
            return

        args = ctx.payload.get("args", [])
        if len(args) < 2:
            ctx.publish(
                {
                    "type": "cmd./resync.error",
                    "text": "Usage: /resync <item|mob> <template_id|all>",
                    "data": {"error": "Missing arguments.", "code": "invalid_args"},
                }
            )
            return

        target_type = str(args[0]).lower()
        target_selector = str(args[1]).lower()
        if target_type not in ("item", "mob"):
            ctx.publish(
                {
                    "type": "cmd./resync.error",
                    "text": "Template type must be item or mob.",
                    "data": {"error": "Unsupported resync type.", "code": "invalid_type"},
                }
            )
            return

        template_id = None
        if target_selector != "all":
            try:
                template_id = int(target_selector)
            except (TypeError, ValueError):
                ctx.publish(
                    {
                        "type": "cmd./resync.error",
                        "text": "Template ID must be a number or 'all'.",
                        "data": {"error": "Invalid template ID.", "code": "invalid_id"},
                    }
                )
                return

        try:
            if target_type == "item":
                result = ResyncItemTemplatesAction().execute(
                    player_id=ctx.player.id,
                    template_id=template_id,
                )
            else:
                result = ResyncMobTemplatesAction().execute(
                    player_id=ctx.player.id,
                    template_id=template_id,
                )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./resync.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )
