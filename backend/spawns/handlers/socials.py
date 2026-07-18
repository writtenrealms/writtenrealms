from __future__ import annotations

from spawns.actions.base import ActionError
from spawns.actions.socials import SocialAction
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler
from spawns.socials import list_social_commands, resolve_social_for_command


def _social_target(ctx: CommandContext) -> str | None:
    args = ctx.payload.get("args") or []
    if args:
        # WR1 socials intentionally consume only the first target token.
        return str(args[0])
    target = ctx.payload.get("target")
    return str(target) if target not in (None, "") else None


def _publish_social_error(
    ctx: CommandContext,
    error: ActionError,
    *,
    social: str | None = None,
) -> None:
    ctx.publish(
        {
            "type": "cmd.dosocial.error",
            "text": error.message,
            "data": {
                "error": error.message,
                "code": error.code,
                "social": social,
                **error.data,
            },
        }
    )


def _execute_social(
    ctx: CommandContext,
    social: dict,
    *,
    target: str | None,
) -> None:
    try:
        result = SocialAction().execute(ctx.actor, social, target)
    except ActionError as error:
        _publish_social_error(ctx, error, social=social.get("command"))
        return

    publish_events(
        result.events,
        actor_key=ctx.actor_key,
        connection_id=ctx.connection_id,
    )


def handle_dynamic_social_command(ctx: CommandContext) -> bool:
    """Resolve and execute an otherwise-unhandled text command as a social."""
    social = resolve_social_for_command(ctx.world, ctx.payload.get("command"))
    if social is None:
        return False
    _execute_social(ctx, social, target=_social_target(ctx))
    return True


@register_handler
class SocialHandler(CommandHandler):
    """Structured command entry point; bare text socials use dynamic resolution."""

    command_type = "social"
    supported_actor_types = ("player", "mob")

    def handle(self, ctx: CommandContext) -> None:
        command = ctx.payload.get("social") or ctx.payload.get("command")
        social = resolve_social_for_command(ctx.world, command)
        if social is None:
            error = ActionError("That social is not defined.", code="unknown_social")
            _publish_social_error(ctx, error, social=str(command or ""))
            return
        _execute_social(ctx, social, target=_social_target(ctx))


@register_handler
class SocialsHandler(CommandHandler):
    command_type = "socials"
    text_commands = ("socials",)
    supported_actor_types = ("player", "mob")
    help = {
        "name": "Socials",
        "format": "socials",
        "description": "List the social commands defined for this world.",
        "details": [
            "Use a social by typing its command, optionally followed by a target.",
        ],
        "examples": ["socials", "wave", "wave guard"],
    }

    def handle(self, ctx: CommandContext) -> None:
        commands = list_social_commands(ctx.world)
        if not commands:
            text = "No socials defined."
        else:
            rows = [
                "  ".join(commands[index:index + 5])
                for index in range(0, len(commands), 5)
            ]
            text = "Socials commands:\n" + "\n".join(rows)
        ctx.publish(
            {
                "type": "cmd.socials.success",
                "text": text,
                "data": {"socials": commands},
            }
        )
