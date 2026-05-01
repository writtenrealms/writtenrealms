from spawns.actions.base import ActionError
from spawns.actions.combat import KillAction
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


@register_handler
class KillHandler(CommandHandler):
    command_type = "kill"
    text_commands = ("kill",)
    help = {
        "name": "Kill",
        "format": "kill <mob>",
        "description": "Fight a mob to the death using the current placeholder combat rules.",
        "examples": [
            "kill rat",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        args = ctx.payload.get("args", [])
        target = ctx.payload.get("target")
        if not target and args:
            target = " ".join(args)

        try:
            result = KillAction().execute(ctx.player.id, target)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.kill.error",
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
