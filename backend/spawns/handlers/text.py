"""
Text command handler.

Handles raw text input from players - the primary command interface.
"""
from spawns.handlers.base import CommandHandler, CommandContext
from spawns.handlers.registry import register_handler, resolve_text_handler
from spawns.triggers import execute_command_fallback_trigger


def _parse_text_command(text: str) -> tuple[str | None, list[str], str]:
    stripped = text.strip()
    if not stripped:
        return None, [], ""
    parts = stripped.split()
    return parts[0].lower(), parts[1:], stripped


@register_handler
class TextCommandHandler(CommandHandler):
    """
    Handle text command - raw text input from player.

    Flow:
    1. Parse text into command + args
    2. Resolve command against registry-declared text commands
    3. Map text args into handler payload fields
    4. Delegate to the resolved handler
    """
    command_type = "text"
    supported_actor_types = ("player", "mob")

    def handle(self, ctx: CommandContext) -> None:
        cmd_text = ctx.payload.get("text", "")
        command, args, raw_text = _parse_text_command(cmd_text)
        if not command:
            return

        ctx.payload["args"] = args
        ctx.payload["command"] = command
        ctx.payload["raw_text"] = raw_text

        resolved = resolve_text_handler(command, include_builder=True)
        if not resolved:
            if command.startswith("/"):
                ctx.publish(
                    {
                        "type": f"cmd.{command}.error",
                        "text": "Unknown builder command.",
                        "data": {"error": "Unknown builder command.", "code": "unknown_cmd"},
                    }
                )
            else:
                if not ctx.payload.get("skip_triggers"):
                    trigger_result = execute_command_fallback_trigger(
                        actor=ctx.actor,
                        text=raw_text,
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

                ctx.publish(
                    {
                        "type": "cmd.text.echo",
                        "text": cmd_text,
                        "data": {
                            "original_command": cmd_text,
                        },
                    }
                )
            return

        resolved_command, handler = resolved
        ctx.payload["command"] = resolved_command

        if ctx.actor_type not in getattr(handler, "supported_actor_types", ("player",)):
            ctx.publish(
                {
                    "type": f"cmd.{resolved_command}.error",
                    "text": f"{ctx.actor_type.capitalize()}s cannot execute {resolved_command}.",
                    "data": {
                        "error": f"Unsupported actor type: {ctx.actor_type}.",
                        "code": "unsupported_actor",
                    },
                }
            )
            return

        if handler.command_type == "look" and args:
            ctx.payload["target"] = " ".join(args)
        elif handler.command_type == "move":
            ctx.payload["direction"] = resolved_command
        elif handler.command_type == "drop" and args:
            ctx.payload["item"] = " ".join(args)
        elif handler.command_type == "get" and args:
            ctx.payload["selector"] = args[0]
            if len(args) > 1:
                ctx.payload["source"] = " ".join(args[1:])
        elif handler.command_type == "put" and args:
            ctx.payload["selector"] = args[0]
            if len(args) > 1:
                ctx.payload["target"] = " ".join(args[1:])
        elif handler.command_type == "help" and args:
            ctx.payload["target"] = args[0]

        handler.handle(ctx)
