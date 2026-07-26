from spawns.actions.base import ActionError
from spawns.actions.combat import FleeAction, KillAction
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


@register_handler
class KillHandler(CommandHandler):
    command_type = "kill"
    text_commands = ("kill",)
    help = {
        "name": "Kill",
        "format": "kill <target>",
        "description": (
            "Fight a mob, or engage the opposing contestant while inside an "
            "active duel."
        ),
        "examples": [
            "kill rat",
            "kill Rival",
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


@register_handler
class FleeHandler(CommandHandler):
    command_type = "flee"
    text_commands = ("flee",)
    help = {
        "name": "Flee",
        "format": "flee",
        "description": "Prepare to escape combat, then flee to a random adjacent room.",
        "examples": [
            "flee",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = FleeAction().execute(ctx.player.id)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.flee.error",
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
