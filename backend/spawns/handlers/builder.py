"""
Builder command handlers.

Builder commands start with "/" and require a builder character.
"""
from spawns.actions.base import ActionError
from spawns.actions.builder import (
    CmdAction,
    EchoAction,
    GrantItemAction,
    JumpAction,
    LoadTemplateAction,
    PurgeAction,
    ResyncItemTemplatesAction,
    ResyncMobTemplatesAction,
    SetClassAction,
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
from spawns.handlers.permissions import (
    builder_permission_error,
    can_execute_builder_command,
    has_builder_access,
)
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
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if not args:
        return None, None, None, None, None

    def _extract_target(tokens: list[str]) -> tuple[str | None, list[str]] | None:
        remaining: list[str] = []
        target: str | None = None
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            if token == "--target":
                if idx + 1 >= len(tokens):
                    return None
                target = tokens[idx + 1]
                idx += 2
                continue
            if token.startswith("--target="):
                target = token.split("=", 1)[1].strip()
                if not target:
                    return None
                idx += 1
                continue
            remaining.append(token)
            idx += 1
        return target, remaining

    operation = args[0].lower()
    if operation == "show":
        parsed = _extract_target(args[1:])
        if parsed is None:
            return None, None, None, None, None
        target, tokens = parsed
        if len(tokens) < 1:
            return None, None, None, None, None
        return operation, tokens[0], target, None, None

    if operation in {"get", "clear"}:
        parsed = _extract_target(args[1:])
        if parsed is None:
            return None, None, None, None, None
        target, tokens = parsed
        if len(tokens) < 2:
            return None, None, None, None, None
        return operation, tokens[0], target, tokens[1], None

    if operation == "add":
        parsed = _extract_target(args[1:])
        if parsed is None:
            return None, None, None, None, None
        target, tokens = parsed
        if len(tokens) < 2:
            return None, None, None, None, None
        amount = " ".join(tokens[2:]).strip() if len(tokens) > 2 else "1"
        return operation, tokens[0], target, tokens[1], amount

    if operation == "set":
        lhs, rhs = _split_delimited_args(args[1:])
        if rhs is not None:
            parsed = _extract_target([token for token in (lhs or "").split() if token])
            if parsed is None:
                return None, None, None, None, None
            target, tokens = parsed
            if len(tokens) < 2:
                return None, None, None, None, None
            return operation, tokens[0], target, tokens[1], rhs
        parsed = _extract_target(args[1:])
        if parsed is None:
            return None, None, None, None, None
        target, tokens = parsed
        if len(tokens) < 3:
            return None, None, None, None, None
        return operation, tokens[0], target, tokens[1], " ".join(tokens[2:]).strip()

    return None, None, None, None, None


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


def _parse_setclass_args(ctx: CommandContext) -> tuple[str | None, str | None]:
    class_name = ctx.payload.get("class")
    target = ctx.payload.get("target")
    if class_name is not None:
        return str(class_name), str(target).strip() if target else None

    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if not args:
        return None, None
    if len(args) == 1:
        return args[0], None
    return args[-1], " ".join(args[:-1]).strip()


def _parse_grantitem_args(ctx: CommandContext) -> tuple[str | None, str | None]:
    target = ctx.payload.get("target")
    item = (
        ctx.payload.get("item")
        or ctx.payload.get("item_id")
        or ctx.payload.get("template_id")
    )
    if target is not None and item is not None:
        return str(target).strip(), str(item).strip()

    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if len(args) < 2:
        return None, None
    if len(args) >= 3 and args[-2].lower() == "item":
        return " ".join(args[:-2]).strip(), args[-1]
    return " ".join(args[:-1]).strip(), args[-1]


@register_handler
class LoadHandler(CommandHandler):
    command_type = "/load"
    text_commands = ("/load",)
    builder_only = True
    allow_script_source = True
    supported_actor_types = ("player", "mob", "room")
    help = {
        "name": "Load",
        "format": "/load <item|mob> <template_id|slug> [cmd]",
        "description": (
            "Load an item or mob template. Players and mobs load items into inventory; "
            "rooms load items onto the ground. "
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
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
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
                actor=ctx.actor,
                runtime_world=ctx.world,
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
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )


@register_handler
class GrantItemHandler(CommandHandler):
    command_type = "/grantitem"
    text_commands = ("/grantitem",)
    builder_only = True
    allow_script_source = True
    supported_actor_types = ("player", "mob", "room")
    help = {
        "name": "Grant Item",
        "format": "/grantitem <target> <item_template_id|item_slug>",
        "description": (
            "Load an item template or definition into a target player or mob inventory. "
            "The target is resolved in the issuer's current room."
        ),
        "examples": [
            "/grantitem player.123 starter-blade",
            "/grantitem aria starter-blade",
            "/grantitem quartermaster supply-token",
            "/cmd room -- /grantitem player.123 starter-blade",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
            return

        target, item = _parse_grantitem_args(ctx)
        if not target or not item:
            ctx.publish(
                {
                    "type": "cmd./grantitem.error",
                    "text": "Usage: /grantitem <target> <item_template_id|item_slug>",
                    "data": {"error": "Missing target or item.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = GrantItemAction().execute(
                actor=ctx.actor,
                target_selector=target,
                item_id=item,
                runtime_world=ctx.world,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./grantitem.error",
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
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
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
    allow_script_source = True
    supported_actor_types = ("player", "mob", "room", "zone", "world")
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
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
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
                runtime_world=ctx.world,
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
    allow_script_source = True
    supported_actor_types = ("player", "mob", "room", "zone", "world")
    help = {
        "name": "State",
        "format": "/state <show|get|set|clear|add> <world|zone|room|character> [--target <target>] [key] [-- value]",
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
            "/state set character --target aria pull_lever true",
            "/state clear room lever_pulled",
        ],
    }

    def _can_execute_state_command(self, ctx: CommandContext) -> bool:
        if has_builder_access(ctx.player):
            return True
        return bool(
            ctx.script_source
            and self.allow_script_source
            and ctx.actor_type != "player"
        )

    def handle(self, ctx: CommandContext) -> None:
        if not self._can_execute_state_command(ctx):
            ctx.publish(builder_permission_error(self.command_type))
            return

        operation, scope, target, key, value = _parse_state_args(ctx)
        if not operation or not scope:
            ctx.publish(
                {
                    "type": "cmd./state.error",
                    "text": "Usage: /state <show|get|set|clear|add> <world|zone|room|character> [--target <target>] [key] [-- value]",
                    "data": {"error": "Missing or invalid state arguments.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = StateAction().execute(
                actor=ctx.actor,
                operation=operation,
                scope=scope,
                target_selector=target,
                key=key,
                value=value,
                amount=value,
                runtime_world=ctx.world,
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
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
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
class SetClassHandler(CommandHandler):
    command_type = "/setclass"
    text_commands = ("/setclass",)
    builder_only = True
    allow_script_source = True
    supported_actor_types = ("player", "room")
    help = {
        "name": "Set Class",
        "format": "/setclass <class> | /setclass <player> <class>",
        "description": (
            "Set your class, or set a player in the current room to a class. "
            "The player's vitals are restored from the newly computed stats, "
            "and known abilities are cleared."
        ),
        "examples": [
            "/setclass hoplite",
            "/setclass mystic",
            "/setclass aria tidecaller",
            "/setclass player.123 warlord",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
            return

        class_name, target = _parse_setclass_args(ctx)
        if not class_name:
            ctx.publish(
                {
                    "type": "cmd./setclass.error",
                    "text": "Usage: /setclass <class> | /setclass <player> <class>",
                    "data": {"error": "Missing class.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = SetClassAction().execute(
                actor=ctx.actor,
                class_selector=class_name,
                target_selector=target,
                runtime_world=ctx.world,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./setclass.error",
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
    allow_script_source = True
    supported_actor_types = ("player", "mob", "room", "zone", "world")
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
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
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
                runtime_world=ctx.world,
                skip_triggers=bool(ctx.payload.get("skip_triggers")),
                script_source=ctx.script_source,
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
        "format": "/jump <room_id|direction>",
        "description": (
            "Instantly move yourself to another room by room ID or adjacent direction."
        ),
        "examples": [
            "/jump 50201",
            "/jump room.50201",
            "/jump east",
            "/jump e",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
            return

        room_selector = ctx.payload.get("to")
        if not room_selector:
            args = ctx.payload.get("args", [])
            if not args:
                ctx.publish(
                    {
                        "type": "cmd./jump.error",
                        "text": "Usage: /jump <room_id|direction>",
                        "data": {
                            "error": "Missing room ID or direction.",
                            "code": "invalid_args",
                        },
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
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
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
