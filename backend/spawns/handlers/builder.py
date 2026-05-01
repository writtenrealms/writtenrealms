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
    SetLevelAction,
    StateAction,
)
from spawns.events import publish_events
from spawns.handlers.base import (
    ChoiceResolutionError,
    CommandContext,
    CommandHandler,
    resolve_unambiguous_choice,
)
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
    try:
        resolved_scope = resolve_unambiguous_choice(
            first,
            choices=("room", "zone", "world"),
        )
    except ChoiceResolutionError:
        resolved_scope = None
    if resolved_scope:
        if len(args) < 2:
            return None, None
        return resolved_scope, " ".join(args[1:]).strip()

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


def _parse_state_args(
    ctx: CommandContext,
) -> tuple[str | None, str | None, str | None, str | None]:
    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if not args:
        return None, None, None, None

    operation = args[0].lower()
    if operation == "show":
        if len(args) < 2:
            return None, None, None, None
        return operation, args[1], None, None

    if operation in {"get", "clear"}:
        if len(args) < 3:
            return None, None, None, None
        return operation, args[1], args[2], None

    if operation == "add":
        if len(args) < 3:
            return None, None, None, None
        amount = " ".join(args[3:]).strip() if len(args) > 3 else "1"
        return operation, args[1], args[2], amount

    if operation == "set":
        lhs, rhs = _split_delimited_args(args[1:])
        if rhs is not None:
            lhs_tokens = [token for token in (lhs or "").split() if token]
            if len(lhs_tokens) < 2:
                return None, None, None, None
            return operation, lhs_tokens[0], lhs_tokens[1], rhs
        if len(args) < 4:
            return None, None, None, None
        return operation, args[1], args[2], " ".join(args[3:]).strip()

    return None, None, None, None


def _parse_setlevel_args(ctx: CommandContext) -> tuple[str | None, str | None]:
    level = ctx.payload.get("level")
    target = ctx.payload.get("target")
    if level is not None:
        return str(level), str(target).strip() if target else None

    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if not args:
        return None, None
    if len(args) == 1:
        return args[0], None
    return args[-1], " ".join(args[:-1]).strip()


@register_handler
class LoadHandler(CommandHandler):
    command_type = "/load"
    text_commands = ("/load",)
    builder_only = True
    help = {
        "name": "Load",
        "format": "/load <item|mob> <template_id|slug> [cmd]",
        "description": (
            "Load an item or mob template into your current room. "
            "An optional trailing command is attached to the loaded entity."
        ),
        "examples": [
            "/load item 123",
            "/load item starter-blade",
            "/load mob 456",
            "/load mob camp-quartermaster",
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
                        "text": "Usage: /load <item|mob> <template_id|slug> [cmd]",
                        "data": {"error": "Missing arguments.", "code": "invalid_args"},
                    }
                )
                return
            template_type = args[0]
            template_id = args[1]
            if len(args) > 2:
                cmd = " ".join(args[2:])

        try:
            template_type = resolve_unambiguous_choice(
                str(template_type).lower(),
                choices=("item", "mob"),
            )
        except ChoiceResolutionError:
            template_type = str(template_type).lower()

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
                template_id=template_id,
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
class StateHandler(CommandHandler):
    command_type = "/state"
    text_commands = ("/state",)
    builder_only = True
    supported_actor_types = ("player", "mob")
    help = {
        "name": "State",
        "format": "/state <show|get|set|clear|add> <world|zone|room|character> [key] [-- value]",
        "description": (
            "Inspect or mutate scoped state in the current world, zone, room, or character context. "
            "Use -- when the value contains spaces."
        ),
        "examples": [
            "/state show world",
            "/state get world weather",
            "/state set world weather -- rainy",
            "/state set room lever_pulled true",
            "/state add character rumor_count 1",
            "/state clear room lever_pulled",
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
                    "type": "cmd./state.error",
                    "text": "You do not have permission to use builder commands.",
                    "data": {"error": "Builder permissions required."},
                }
            )
            return

        operation, scope, key, value = _parse_state_args(ctx)
        if not operation or not scope:
            ctx.publish(
                {
                    "type": "cmd./state.error",
                    "text": "Usage: /state <show|get|set|clear|add> <world|zone|room|character> [key] [-- value]",
                    "data": {"error": "Missing or invalid state arguments.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = StateAction().execute(
                actor=ctx.actor,
                operation=operation,
                scope=scope,
                key=key,
                value=value,
                amount=value,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./state.error",
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
class SetLevelHandler(CommandHandler):
    command_type = "/setlevel"
    text_commands = ("/setlevel",)
    builder_only = True
    help = {
        "name": "Set Level",
        "format": "/setlevel <level> | /setlevel <target> <level>",
        "description": (
            "Set your level, or set a player or mob in the current room to a level. "
            "Player targets have their XP moved to that level's threshold and their vitals restored."
        ),
        "examples": [
            "/setlevel 5",
            "/setlevel joe 3",
            "/setlevel guard 8",
            "/setlevel mob.123 2",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not has_builder_access(ctx.player):
            ctx.publish(
                {
                    "type": "cmd./setlevel.error",
                    "text": "You do not have permission to use builder commands.",
                    "data": {"error": "Builder permissions required."},
                }
            )
            return

        level, target = _parse_setlevel_args(ctx)
        if not level:
            ctx.publish(
                {
                    "type": "cmd./setlevel.error",
                    "text": "Usage: /setlevel <level> | /setlevel <target> <level>",
                    "data": {"error": "Missing level.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = SetLevelAction().execute(
                actor=ctx.player,
                level=level,
                target_selector=target,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./setlevel.error",
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
        try:
            target_type = resolve_unambiguous_choice(
                target_type,
                choices=("item", "mob"),
            )
        except ChoiceResolutionError:
            pass
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
