"""
Builder command handlers.

Builder commands start with "/" and require a builder character.
"""
from spawns.actions.base import ActionError
from spawns.actions.builder import (
    BuilderStatsAction,
    CmdAction,
    EchoAction,
    GrantItemAction,
    InvisibleAction,
    JumpAction,
    LoadDefinitionAction,
    PurgeAction,
    RegenAction,
    SendAction,
    MOB_SET_FIELD_CHOICES,
    PLAYER_SET_FIELD_CHOICES,
    SetClassAction,
    SetLevelAction,
    SetStatAction,
    StateAction,
    WizKillAction,
)
from spawns.events import GameEvent, publish_events
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
from spawns.state_payloads import build_state_sync, get_player_with_related
from spawns.text_output import render_event_text
from worlds.instances import reset_instance

SCOPED_ECHO_ALIASES = {
    "/wecho": "world",
    "/zecho": "zone",
}
SCOPED_CMD_ALIASES = {
    "/rcmd": "room",
    "/wcmd": "world",
    "/zcmd": "zone",
}

SET_STAT_HELP_DETAILS = [
    f"Player fields: {', '.join(PLAYER_SET_FIELD_CHOICES)}.",
    f"Mob fields: {', '.join(MOB_SET_FIELD_CHOICES)}.",
    "Attribute keys can be set with attribute.<key>, attributes.<key>, or attr.<key>.",
    "Use attributes -- <json object> to replace the full attributes map.",
    "Current resources cannot exceed their max; set the max first when raising both.",
    "Lowering a mob resource max clamps the current resource down to that max.",
]

STATE_COMMAND_USAGE = (
    "Usage: /state <show|get|set|clear|add> <world|zone|room> [key] [value]; "
    "for character state use /state <show|get|set|clear|add> character <target> [key] [value]"
)
STATE_COMMAND_SCOPES = {"world", "zone", "room", "character"}


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

    def _contains_target_option(tokens: list[str]) -> bool:
        return any(token == "--target" or token.startswith("--target=") for token in tokens)

    def _parse_scope_target_and_tokens(tokens: list[str]) -> tuple[str, str | None, list[str]] | None:
        if not tokens or _contains_target_option(tokens):
            return None
        scope = tokens[0].lower()
        if scope not in STATE_COMMAND_SCOPES:
            return None
        remaining = tokens[1:]
        if scope != "character":
            return scope, None, remaining
        if not remaining:
            return None
        return scope, remaining[0], remaining[1:]

    operation = args[0].lower()
    if operation == "show":
        parsed = _parse_scope_target_and_tokens(args[1:])
        if parsed is None:
            return None, None, None, None, None
        scope, target, tokens = parsed
        if tokens:
            return None, None, None, None, None
        return operation, scope, target, None, None

    if operation in {"get", "clear"}:
        parsed = _parse_scope_target_and_tokens(args[1:])
        if parsed is None:
            return None, None, None, None, None
        scope, target, tokens = parsed
        if len(tokens) != 1:
            return None, None, None, None, None
        return operation, scope, target, tokens[0], None

    if operation == "add":
        parsed = _parse_scope_target_and_tokens(args[1:])
        if parsed is None:
            return None, None, None, None, None
        scope, target, tokens = parsed
        if len(tokens) < 1:
            return None, None, None, None, None
        amount = " ".join(tokens[1:]).strip() if len(tokens) > 1 else "1"
        return operation, scope, target, tokens[0], amount

    if operation == "set":
        lhs, rhs = _split_delimited_args(args[1:])
        if rhs is not None:
            parsed = _parse_scope_target_and_tokens([token for token in (lhs or "").split() if token])
            if parsed is None:
                return None, None, None, None, None
            scope, target, tokens = parsed
            if len(tokens) != 1:
                return None, None, None, None, None
            return operation, scope, target, tokens[0], rhs
        parsed = _parse_scope_target_and_tokens(args[1:])
        if parsed is None:
            return None, None, None, None, None
        scope, target, tokens = parsed
        if len(tokens) < 2:
            return None, None, None, None, None
        return operation, scope, target, tokens[0], " ".join(tokens[1:]).strip()

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


def _parse_stats_args(ctx: CommandContext) -> str | None:
    target = ctx.payload.get("target")
    if target is not None:
        return str(target).strip() or None
    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if not args:
        return None
    return " ".join(args).strip()


def _parse_regen_args(ctx: CommandContext) -> tuple[str | None, str | None]:
    target = ctx.payload.get("target")
    resource = ctx.payload.get("resource")
    if target is not None or resource is not None:
        return (
            str(target).strip() if target is not None else None,
            str(resource).strip() if resource is not None else None,
        )

    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if not args:
        return None, None
    if len(args) == 1:
        return args[0], None
    return " ".join(args[:-1]).strip(), args[-1]


def _parse_setstat_args(ctx: CommandContext) -> tuple[str | None, str | None, object | None]:
    target = ctx.payload.get("target")
    field_name = ctx.payload.get("field") or ctx.payload.get("stat")
    value = ctx.payload.get("value")
    if target is not None and field_name is not None:
        return (
            str(target).strip(),
            str(field_name).strip(),
            value,
        )

    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if len(args) < 3:
        return None, None, None

    lhs, rhs = _split_delimited_args(args)
    if rhs is not None:
        lhs_args = [token for token in str(lhs or "").split() if token.strip()]
        if len(lhs_args) < 2:
            return None, None, None
        return " ".join(lhs_args[:-1]).strip(), lhs_args[-1], rhs

    return " ".join(args[:-2]).strip(), args[-2], args[-1]


def _payload_item_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item for item in str(value).split() if item.strip()]


def _parse_grantitem_args(ctx: CommandContext) -> tuple[str | None, list[str]]:
    target = ctx.payload.get("target")
    items = (
        ctx.payload.get("items")
        or ctx.payload.get("item_ids")
        or ctx.payload.get("definition_ids")
    )
    item = (
        ctx.payload.get("item")
        or ctx.payload.get("item_id")
        or ctx.payload.get("definition_id")
    )
    if target is not None:
        item_ids = _payload_item_list(items)
        if not item_ids and item is not None:
            item_ids = [str(item).strip()]
        return str(target).strip(), [item_id for item_id in item_ids if item_id]

    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if len(args) < 2:
        return None, []

    delimited_target, delimited_items = _split_delimited_args(args)
    if delimited_items is not None:
        return delimited_target, [
            item_id
            for item_id in delimited_items.split()
            if item_id.strip()
        ]

    if len(args) >= 3 and args[-2].lower() == "item":
        return " ".join(args[:-2]).strip(), [args[-1]]
    return " ".join(args[:-1]).strip(), [args[-1]]


def _parse_kill_args(ctx: CommandContext) -> tuple[str | None, str | None]:
    target = ctx.payload.get("target")
    message = ctx.payload.get("message") or ctx.payload.get("msg")
    if target is not None:
        return str(target).strip(), str(message).strip() if message else None

    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if not args:
        return None, None
    target_text, message_text = _split_delimited_args(args)
    if message_text is not None:
        return target_text, message_text
    if len(args) > 1:
        return args[0], " ".join(args[1:]).strip()
    return args[0], None


def _parse_send_args(ctx: CommandContext) -> tuple[str | None, str | None]:
    target = ctx.payload.get("target") or ctx.payload.get("to")
    message = ctx.payload.get("message") or ctx.payload.get("text")
    if target is not None:
        return str(target).strip(), str(message).strip() if message else None

    args = [str(arg).strip() for arg in list(ctx.payload.get("args", [])) if str(arg).strip()]
    if not args:
        return None, None
    target_text, message_text = _split_delimited_args(args)
    if message_text is not None:
        return target_text, message_text
    if len(args) > 1:
        return args[0], " ".join(args[1:]).strip()
    return args[0], None


@register_handler
class LoadHandler(CommandHandler):
    command_type = "/load"
    text_commands = ("/load",)
    builder_only = True
    allow_script_source = True
    supported_actor_types = ("player", "mob", "room")
    help = {
        "name": "Load",
        "format": "/load <item|mob> <definition_id|slug> [cmd]",
        "description": (
            "Load an item or mob definition. Players and mobs load items into inventory; "
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

        definition_type = ctx.payload.get("definition_type")
        definition_id = ctx.payload.get("definition_id")
        cmd = ctx.payload.get("cmd")

        if not definition_type or not definition_id:
            args = ctx.payload.get("args", [])
            if len(args) < 2:
                ctx.publish(
                    {
                        "type": "cmd./load.error",
                        "text": "Usage: /load <item|mob> <definition_id|slug> [cmd]",
                        "data": {"error": "Missing arguments.", "code": "invalid_args"},
                    }
                )
                return
            definition_type = args[0]
            definition_id = args[1]
            if len(args) > 2:
                cmd = " ".join(args[2:])

        try:
            definition_type = resolve_unambiguous_choice(
                str(definition_type).lower(),
                choices=("item", "mob"),
            )
        except ChoiceResolutionError:
            definition_type = str(definition_type).lower()

        if definition_type not in ("item", "mob"):
            ctx.publish(
                {
                    "type": "cmd./load.error",
                    "text": "Definition type must be item or mob.",
                    "data": {"error": "Invalid definition type.", "code": "invalid_type"},
                }
            )
            return

        try:
            result = LoadDefinitionAction().execute(
                actor=ctx.actor,
                runtime_world=ctx.world,
                definition_type=definition_type,
                definition_id=definition_id,
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
        "format": "/grantitem <target> <item_definition_id|item_slug> | /grantitem <target> -- <item_selector>...",
        "description": (
            "Load an item definition into a target player or mob inventory. "
            "The target is resolved in the issuer's current room."
        ),
        "examples": [
            "/grantitem player.123 starter-blade",
            "/grantitem aria starter-blade",
            "/grantitem quartermaster supply-token",
            "/grantitem player.123 -- starter-blade starter-shield",
            "/cmd room -- /grantitem player.123 starter-blade",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
            return

        target, items = _parse_grantitem_args(ctx)
        if not target or not items:
            ctx.publish(
                {
                    "type": "cmd./grantitem.error",
                    "text": "Usage: /grantitem <target> <item_definition_id|item_slug> or /grantitem <target> -- <item_selector>...",
                    "data": {"error": "Missing target or item.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = GrantItemAction().execute_many(
                actor=ctx.actor,
                target_selector=target,
                item_ids=items,
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
class SendHandler(CommandHandler):
    command_type = "/send"
    text_commands = ("/send",)
    builder_only = True
    allow_script_source = True
    supported_actor_types = ("player", "mob", "room", "zone", "world")
    help = {
        "name": "Send",
        "format": "/send <player> <message>",
        "description": "Send private text to one connected player in the issuer's runtime world.",
        "examples": [
            "/send aria The altar hums beneath your hand.",
            "/send player.123 -- You hear distant surf.",
            "/cmd room -- /send {{ actor_key }} -- You feel watched.",
        ],
    }

    def _can_execute_send_command(self, ctx: CommandContext) -> bool:
        if has_builder_access(ctx.player):
            return True
        return bool(
            ctx.script_source
            and self.allow_script_source
            and ctx.actor_type in {"mob", "room", "zone", "world"}
        )

    def handle(self, ctx: CommandContext) -> None:
        if not self._can_execute_send_command(ctx):
            ctx.publish(builder_permission_error(self.command_type))
            return

        target, message = _parse_send_args(ctx)
        if not target or not message:
            ctx.publish(
                {
                    "type": "cmd./send.error",
                    "text": "Usage: /send <player> <message>",
                    "data": {"error": "Missing target or message.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = SendAction().execute(
                actor=ctx.actor,
                target_selector=target,
                message=message,
                runtime_world=ctx.world,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./send.error",
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
class WizKillHandler(CommandHandler):
    command_type = "/kill"
    text_commands = ("/kill",)
    builder_only = True
    allow_script_source = True
    supported_actor_types = ("player", "mob", "room")
    help = {
        "name": "Kill",
        "format": "/kill <target> [-- message]",
        "description": (
            "Instantly kill a player target in the issuer's current room. "
            "The target is moved through the normal death-room pipeline."
        ),
        "examples": [
            "/kill player.123",
            "/kill aria -- The pit swallows you whole.",
            "/cmd room -- /kill {{ actor_key }} -- The pit swallows you whole.",
        ],
    }

    def _can_execute_kill_command(self, ctx: CommandContext) -> bool:
        if has_builder_access(ctx.player):
            return True
        return bool(
            ctx.script_source
            and self.allow_script_source
            and ctx.actor_type in {"mob", "room"}
        )

    def handle(self, ctx: CommandContext) -> None:
        if not self._can_execute_kill_command(ctx):
            ctx.publish(builder_permission_error(self.command_type))
            return

        target, message = _parse_kill_args(ctx)
        if not target:
            ctx.publish(
                {
                    "type": "cmd./kill.error",
                    "text": "Usage: /kill <target> [-- message]",
                    "data": {"error": "Missing target.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = WizKillAction().execute(
                actor=ctx.actor,
                target_selector=target,
                message=message,
                runtime_world=ctx.world,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./kill.error",
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
        "format": "/state <show|get|set|clear|add> <world|zone|room> [key] [-- value] | /state <show|get|set|clear|add> character <target> [key] [-- value]",
        "description": (
            "Inspect or mutate scoped state in the current world, zone, room, or character context. "
            "Character state always requires an explicit target. "
            "Use -- when the value contains spaces."
        ),
        "examples": [
            "/state show world",
            "/state get world weather",
            "/state set world weather -- rainy",
            "/state set room lever_pulled true",
            "/state add character self rumor_count 1",
            "/state set character aria pull_lever true",
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
                    "text": STATE_COMMAND_USAGE,
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
class BuilderStatsHandler(CommandHandler):
    command_type = "/stats"
    text_commands = ("/stats",)
    builder_only = True
    help = {
        "name": "Builder Stats",
        "format": "/stats [target|player.<id>|mob.<id>]",
        "description": (
            "Show a full builder stat readout for yourself, a character in your current room, "
            "or a player/mob key anywhere in your current world."
        ),
        "examples": [
            "/stats",
            "/stats guard",
            "/stats aria",
            "/stats mob.123",
            "/stats player.456",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
            return

        target = _parse_stats_args(ctx)

        try:
            result = BuilderStatsAction().execute(
                actor=ctx.player,
                target_selector=target,
                runtime_world=ctx.world,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./stats.error",
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
class RegenHandler(CommandHandler):
    command_type = "/regen"
    text_commands = ("/regen",)
    builder_only = True
    allow_mob_actor = True
    supported_actor_types = ("player", "mob")
    help = {
        "name": "Regen",
        "format": "/regen | /regen <target> [health|energy|stamina]",
        "description": (
            "Restore your resources to full, or restore a target player or mob in the current room. "
            "Use an optional resource name to restore only health, energy, or stamina."
        ),
        "examples": [
            "/regen",
            "/regen guard",
            "/regen aria energy",
            "/cmd guard -- /regen self health",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
            return

        target, resource = _parse_regen_args(ctx)
        try:
            result = RegenAction().execute(
                actor=ctx.actor,
                target_selector=target,
                resource=resource,
                runtime_world=ctx.world,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./regen.error",
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
class SetStatHandler(CommandHandler):
    command_type = "/set"
    text_commands = ("/set",)
    builder_only = True
    help = {
        "name": "Set Stat",
        "format": "/set <target|player.<id>|mob.<id>> <field|attributes.key> <value>",
        "description": (
            "Set a persisted stat field on a player or mob. Player combat ratings are computed; "
            "set player attributes or equipment instead of direct computed ratings."
        ),
        "details": SET_STAT_HELP_DETAILS,
        "examples": [
            "/set guard health 25",
            "/set guard attack_power 8",
            "/set aria health 10",
            "/set player.456 attribute.strength 5",
            "/set mob.123 attributes -- {\"strength\": 4}",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
            return

        target, field_name, value = _parse_setstat_args(ctx)
        if not target or not field_name or value is None:
            ctx.publish(
                {
                    "type": "cmd./set.error",
                    "text": "Usage: /set <target|player.<id>|mob.<id>> <field|attributes.key> <value>",
                    "data": {"error": "Missing target, field, or value.", "code": "invalid_args"},
                }
            )
            return

        try:
            result = SetStatAction().execute(
                actor=ctx.player,
                target_selector=target,
                field_name=field_name,
                value=value,
                runtime_world=ctx.world,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./set.error",
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
class InvisibleHandler(CommandHandler):
    command_type = "/invisible"
    text_commands = ("/invisible", "/inv")
    builder_only = True
    help = {
        "name": "Wiz Invisible",
        "format": "/invisible | /inv",
        "description": "Toggle whether your builder character is visible to regular characters.",
        "examples": [
            "/invisible",
            "/inv",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
            return

        try:
            result = InvisibleAction().execute(player_id=ctx.player.id)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd./invisible.error",
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
class ResetInstanceHandler(CommandHandler):
    command_type = "/reset"
    text_commands = ("/reset",)
    builder_only = True
    help = {
        "name": "Reset Instance",
        "format": "/reset",
        "description": "Reset the current instance run to its initial spawned state.",
        "details": [
            "Only builder characters can use this command directly.",
            (
                "The active run and Instance ID are kept, while spawned mobs, "
                "ground items, combat, door overrides, and instance world state are rebuilt."
            ),
            (
                "Active participants remain in the instance and are moved to "
                "the instance starting room."
            ),
        ],
        "examples": [
            "/reset",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        if not can_execute_builder_command(ctx, self):
            ctx.publish(builder_permission_error(self.command_type))
            return

        try:
            result = reset_instance(player=ctx.player)
        except ValueError as err:
            ctx.publish(
                {
                    "type": "cmd./reset.error",
                    "text": str(err),
                    "data": {"error": str(err), "code": "invalid_instance"},
                }
            )
            return

        events = [
            GameEvent(
                type="cmd./reset.success",
                recipients=[ctx.actor_key],
                data={
                    "reset_scope": "instance",
                    "run_id": result.run_id,
                    "instance_ref": result.instance_ref,
                    "world_id": result.spawned_world_id,
                    "players_reset": len(result.player_ids),
                    "mobs_deleted": result.mobs_deleted,
                    "items_deleted": result.items_deleted,
                    "combat_encounters_deleted": result.combat_encounters_deleted,
                    "spawn_plan_runs_reset": result.spawn_plan_runs_reset,
                    "template_scoped_state_reset": result.template_scoped_state_reset,
                },
                text="Instance reset.",
            )
        ]
        for player_id in result.player_ids:
            player = get_player_with_related(player_id)
            payload = build_state_sync(player).model_dump()
            events.append(
                GameEvent(
                    type="cmd.state.sync.success",
                    recipients=[player.key],
                    data=payload,
                    text=render_event_text(
                        "cmd.state.sync.success",
                        payload,
                        viewer=player,
                    ),
                )
            )

        publish_events(
            events,
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )
