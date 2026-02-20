"""
Communication command handlers (say, yell, emote).
"""
from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.actions.communication import EmoteAction, SayAction, YellAction
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler
from spawns.triggers import execute_mob_event_triggers


def _resolve_message_text(ctx: CommandContext) -> str | None:
    args = ctx.payload.get("args", [])
    # Text-command path should use parsed args, not raw command text.
    if "raw_text" in ctx.payload:
        if not args:
            return None
        return " ".join(args)

    # Structured command path can pass explicit message text.
    message = ctx.payload.get("message")
    if message is not None:
        return str(message)

    if args:
        return " ".join(args)
    text = ctx.payload.get("text")
    if text is None:
        return None
    return str(text)


@register_handler
class SayHandler(CommandHandler):
    command_type = "say"
    text_commands = ("say",)
    supported_actor_types = ("player", "mob")
    help = {
        "name": "Say",
        "format": "say <message>",
        "description": "Say something that everyone in your room can hear.",
        "examples": [
            "say Hello there.",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        text = _resolve_message_text(ctx)

        try:
            result = SayAction().execute(ctx.actor, text)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.say.error",
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

        if not ctx.payload.get("__trigger_source"):
            execute_mob_event_triggers(
                event=adv_consts.MOB_REACTION_EVENT_SAYING,
                actor=ctx.actor,
                room=getattr(ctx.actor, "room_id", None),
                option_text=text,
                connection_id=ctx.connection_id,
            )


@register_handler
class YellHandler(CommandHandler):
    command_type = "yell"
    text_commands = ("yell",)
    supported_actor_types = ("player", "mob")
    help = {
        "name": "Yell",
        "format": "yell <message>",
        "description": "Yell something that everyone in your zone can hear.",
        "examples": [
            "yell Come here!",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        text = _resolve_message_text(ctx)

        try:
            result = YellAction().execute(ctx.actor, text)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.yell.error",
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
class EmoteHandler(CommandHandler):
    command_type = "emote"
    text_commands = ("emote",)
    supported_actor_types = ("player", "mob")
    help = {
        "name": "Emote",
        "format": "emote <message>",
        "description": "Display an in-room action line beginning with your name.",
        "examples": [
            "emote smiles warmly.",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        text = _resolve_message_text(ctx)

        try:
            result = EmoteAction().execute(ctx.actor, text)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.emote.error",
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
