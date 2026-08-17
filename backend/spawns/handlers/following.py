from spawns.actions.base import ActionError
from spawns.actions.following import FollowAction, UnfollowAction
from spawns.events import publish_events
from spawns.handlers.base import (
    TRIGGER_STEP_MODE_TRANSACTIONAL,
    CommandContext,
    CommandHandler,
)
from spawns.handlers.registry import register_handler


def _selector(ctx: CommandContext) -> str | None:
    args = ctx.payload.get("args") or []
    if args:
        return " ".join(str(arg) for arg in args).strip() or None
    target = ctx.payload.get("target")
    return str(target).strip() if target not in (None, "") else None


def _publish_error(
    ctx: CommandContext,
    *,
    command_type: str,
    error: ActionError,
) -> None:
    ctx.publish(
        {
            "type": f"cmd.{command_type}.error",
            "text": error.message,
            "data": {
                "error": error.message,
                "code": error.code,
                **error.data,
            },
        }
    )


@register_handler
class FollowHandler(CommandHandler):
    command_type = "follow"
    text_commands = ("follow",)
    supported_actor_types = ("player",)
    trigger_step_mode = TRIGGER_STEP_MODE_TRANSACTIONAL
    help = {
        "name": "Follow",
        "format": "follow <player|mob>",
        "description": "Follow a player or mob when they move from your room.",
        "details": [
            "Following is movement intent; it does not create a group or party.",
            "Use unfollow to stop following your current leader.",
            "A following chain can contain at most 16 links.",
        ],
        "examples": ["follow hermes", "follow player.123"],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = FollowAction().execute(ctx.player.id, _selector(ctx))
        except ActionError as error:
            _publish_error(
                ctx,
                command_type=self.command_type,
                error=error,
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )


@register_handler
class UnfollowHandler(CommandHandler):
    command_type = "unfollow"
    text_commands = ("unfollow",)
    supported_actor_types = ("player",)
    trigger_step_mode = TRIGGER_STEP_MODE_TRANSACTIONAL
    help = {
        "name": "Unfollow",
        "format": "unfollow [current leader]",
        "description": "Stop following your current player or mob leader.",
        "examples": ["unfollow", "unfollow hermes"],
    }

    def handle(self, ctx: CommandContext) -> None:
        try:
            result = UnfollowAction().execute(ctx.player.id, _selector(ctx))
        except ActionError as error:
            _publish_error(
                ctx,
                command_type=self.command_type,
                error=error,
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )
