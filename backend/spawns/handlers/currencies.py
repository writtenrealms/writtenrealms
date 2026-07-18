"""Player commands for inspecting authored currency balances."""

from spawns.actions.base import ActionError
from spawns.actions.currencies import ListCurrenciesAction
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


@register_handler
class CurrenciesHandler(CommandHandler):
    command_type = "currencies"
    text_commands = ("currencies",)
    help = {
        "name": "Currencies",
        "format": "currencies",
        "description": "Show your current currency balances.",
        "examples": ["currencies"],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = ListCurrenciesAction().execute(ctx.player.id)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.currencies.error",
                    "text": err.message,
                    "data": {
                        "error": err.message,
                        "code": err.code,
                        **err.data,
                    },
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )
