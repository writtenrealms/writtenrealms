"""
Information / observation commands (look, inspect, etc).

Implements information-oriented commands such as look, inventory, and help.
"""
from spawns.command_history import get_player_command_history
from spawns.actions.base import ActionError
from quests.services.discovery import available_room_prompt_opportunities_for_room
from spawns.actions.information import (
    InspectAction,
    InventoryAction,
    LookAction,
    RollAction,
    ScanAction,
    StatsAction,
    WhoAction,
)
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.permissions import has_builder_access
from spawns.handlers.registry import (
    iter_text_handlers,
    register_handler,
    resolve_text_handler,
)
from spawns.triggers import execute_command_fallback_trigger


@register_handler
class LookHandler(CommandHandler):
    command_type = "look"
    text_commands = ("look",)
    help = {
        "name": "Look",
        "format": "look | look <target>",
        "description": (
            "Look at your current room, or at a specific target in it."
        ),
        "examples": [
            "look",
            "look soldier",
            "look sword",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        target = ctx.payload.get("target")
        if target is None:
            args = ctx.payload.get("args", [])
            if args:
                target = " ".join(args)

        try:
            result = LookAction().execute(ctx.player.id, target_selector=target)
        except ActionError as err:
            ctx.publish_error("look", err.message)
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class InspectHandler(CommandHandler):
    command_type = "inspect"
    text_commands = ("inspect",)
    help = {
        "name": "Inspect",
        "format": "inspect",
        "description": "Inspect the current room for quest callouts.",
        "details": [
            "`inspect` is the room-side counterpart to `talk <mob>`.",
        ],
        "examples": [
            "inspect",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        args = ctx.payload.get("args", [])
        if args:
            trigger_result = execute_command_fallback_trigger(
                actor=ctx.actor,
                text=ctx.payload.get("raw_text", "inspect"),
                connection_id=ctx.connection_id,
            )
            if trigger_result.handled:
                if trigger_result.feedback:
                    ctx.publish(
                        {
                            "type": "cmd.text.trigger",
                            "text": trigger_result.feedback,
                            "data": {"text": trigger_result.feedback},
                        }
                    )
                return

        opportunities = available_room_prompt_opportunities_for_room(
            ctx.player,
            getattr(ctx.player, "room_id", None),
        )
        if not opportunities:
            ctx.publish(
                {
                    "type": "cmd.inspect.error",
                    "text": "You don't notice anything here worth inspecting.",
                    "data": {
                        "error": "You don't notice anything here worth inspecting.",
                        "code": "nothing_to_inspect",
                    },
                }
            )
            return

        try:
            result = InspectAction().execute(ctx.player.id)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.inspect.error",
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
class ScanHandler(CommandHandler):
    command_type = "scan"
    text_commands = ("scan",)
    help = {
        "name": "Scan",
        "format": "scan <direction>",
        "description": (
            "Display the characters in an exit room provided you are not "
            "standing in a forest or on a mountain."
        ),
        "examples": [
            "scan east",
            "scan e",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        direction = ctx.payload.get("direction") or ctx.payload.get("target")
        if isinstance(direction, dict):
            direction = direction.get("name") or direction.get("direction")
        if direction is None:
            args = ctx.payload.get("args", [])
            if args:
                direction = args[0]

        try:
            result = ScanAction().execute(ctx.player.id, direction=direction)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.scan.error",
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
class InventoryHandler(CommandHandler):
    command_type = "inventory"
    text_commands = ("inventory", "inv", "i")
    help = {
        "name": "Inventory",
        "format": "inventory | inv | i",
        "description": "Show items currently carried by your character.",
        "examples": ["inventory", "inv", "i"],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = InventoryAction().execute(ctx.player.id)
        except ActionError as err:
            ctx.publish_error("inventory", err.message)
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class StatsHandler(CommandHandler):
    command_type = "stats"
    text_commands = ("stats",)
    help = {
        "name": "Stats",
        "format": "stats",
        "description": "Show your current vitals, attributes, and stats.",
        "examples": ["stats"],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = StatsAction().execute(ctx.player.id)
        except ActionError as err:
            ctx.publish_error("stats", err.message)
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class WhoHandler(CommandHandler):
    command_type = "who"
    text_commands = ("who",)
    help = {
        "name": "Who",
        "format": "who",
        "description": (
            "List all currently logged in players. Players with a ~ in "
            "front of their names are Builders, who can give special "
            "assistance if needed."
        ),
        "examples": ["who"],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = WhoAction().execute(ctx.player.id)
        except ActionError as err:
            ctx.publish_error("who", err.message)
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class RollHandler(CommandHandler):
    command_type = "roll"
    text_commands = ("roll",)
    help = {
        "name": "Roll",
        "format": "roll <size> | roll <num>d<size>",
        "description": (
            "Roll a die by size, or use XdY format where X is the number of "
            "rolls and Y is the die size. If no argument is given, rolls 1d6. "
            "Maximum roll count and die size are 100."
        ),
        "examples": [
            "roll",
            "roll 10",
            "roll 2d6",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        target = ctx.payload.get("target")
        if target is None:
            args = ctx.payload.get("args", [])
            if args:
                target = args[0]

        try:
            result = RollAction().execute(ctx.player.id, target=target)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.roll.error",
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
class HelpHandler(CommandHandler):
    command_type = "help"
    text_commands = ("help",)
    help = {
        "name": "Help",
        "format": "help | help <command>",
        "description": "List available commands or show details for one command.",
        "examples": [
            "help",
            "help look",
            "help /load",
        ],
    }

    def _build_command_list(self, include_builder: bool) -> list[dict]:
        commands: list[dict] = []
        seen_handlers: set[int] = set()

        for command_name, handler in iter_text_handlers(include_builder=include_builder):
            handler_id = id(handler)
            if handler_id in seen_handlers:
                continue
            seen_handlers.add(handler_id)
            help_data = handler.get_help_data(command_name=command_name) or {}
            commands.append(
                {
                    "command": command_name,
                    "format": help_data.get("format", command_name),
                    "description": help_data.get("description", ""),
                }
            )

        return commands

    def _resolve_help_target(self, ctx: CommandContext) -> str | None:
        target = ctx.payload.get("target")
        if target:
            normalized = str(target).strip().lower()
            return "equipment" if normalized == "eq" else normalized or None
        args = ctx.payload.get("args", [])
        if args:
            normalized = str(args[0]).strip().lower()
            return "equipment" if normalized == "eq" else normalized or None
        return None

    def _render_list_text(self, commands: list[dict]) -> str:
        lines = ["Commands:"]
        for entry in commands:
            line = f"* {entry['format']}"
            description = entry.get("description")
            if description:
                line += f" - {description}"
            lines.append(line)
        return "\n".join(lines)

    def handle(self, ctx: CommandContext) -> None:
        include_builder = has_builder_access(ctx.player)
        target = self._resolve_help_target(ctx)

        if target:
            resolved = resolve_text_handler(target, include_builder=True)
            if not resolved:
                ctx.publish_error("help", f"Unknown command: {target}")
                return

            command_name, handler = resolved
            if getattr(handler, "builder_only", False) and not include_builder:
                ctx.publish_error("help", "You do not have permission to view that command.")
                return

            help_data = handler.get_help_data(command_name=command_name)
            if not help_data:
                ctx.publish_error("help", f"No help available for {command_name}.")
                return

            ctx.publish(
                {
                    "type": "cmd.help.success",
                    "text": handler.get_help_text(command_name=command_name),
                    "data": {
                        "command": help_data,
                    },
                }
            )
            return

        commands = self._build_command_list(include_builder=include_builder)
        ctx.publish(
            {
                "type": "cmd.help.success",
                "text": self._render_list_text(commands),
                "data": {"commands": commands},
            }
        )


@register_handler
class HistoryHandler(CommandHandler):
    command_type = "history"
    text_commands = ("history",)
    help = {
        "name": "History",
        "format": "history | !<number>",
        "description": "Show recent commands. Repeat one with !<number>.",
        "examples": [
            "history",
            "!1",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        history = get_player_command_history(ctx.player)
        commands = [
            {
                "index": index,
                "command": command,
            }
            for index, command in enumerate(history, start=1)
        ]

        if commands:
            text = "\n".join(
                f"{entry['index']}. {entry['command']}"
                for entry in commands
            )
        else:
            text = "No command history yet."

        ctx.publish(
            {
                "type": "cmd.history.success",
                "text": text,
                "data": {"commands": commands},
            }
        )
