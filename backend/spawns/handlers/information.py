"""
Information / observation commands (look, inspect, etc).

Implements information-oriented commands such as look, inventory, and help.
"""
from spawns.actions.base import ActionError
from spawns.actions.information import InventoryAction, LookAction, RollAction
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.permissions import has_builder_access
from spawns.handlers.registry import (
    iter_text_handlers,
    register_handler,
    resolve_text_handler,
)


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
        try:
            result = LookAction().execute(ctx.player.id)
        except ActionError as err:
            ctx.publish_error("look", err.message)
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )


@register_handler
class InventoryHandler(CommandHandler):
    command_type = "inventory"
    text_commands = ("inventory",)
    help = {
        "name": "Inventory",
        "format": "inventory",
        "description": "Show items currently carried by your character.",
        "examples": ["inventory"],
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
            return str(target).strip().lower() or None
        args = ctx.payload.get("args", [])
        if args:
            return str(args[0]).strip().lower() or None
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
