from django.db import transaction

from spawns.actions.base import ActionError
from spawns.actions.player_state import RestAction, StandAction
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


@register_handler
class RestHandler(CommandHandler):
    command_type = "rest"
    text_commands = ("rest",)
    text_aliases = {"r": "rest"}
    help = {
        "name": "Rest",
        "format": "rest | r",
        "description": "Rest to recover health, energy, and stamina faster outside combat.",
        "examples": ["rest", "r"],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            with transaction.atomic():
                result = RestAction().execute(ctx.player.id)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.rest.error",
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
class StandHandler(CommandHandler):
    command_type = "stand"
    text_commands = ("stand",)
    help = {
        "name": "Stand",
        "format": "stand",
        "description": "Stop resting and return to standing.",
        "examples": ["stand"],
    }

    def handle(self, ctx: CommandContext) -> None:
        with transaction.atomic():
            result = StandAction().execute(ctx.player.id)

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )
