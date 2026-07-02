"""
Instance command handlers.

These commands bridge the player-facing text command path to the WR2 instance
runtime service in worlds.instances.
"""
from spawns.events import GameEvent, publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler
from spawns.models import Player
from spawns.state_payloads import build_state_sync, get_player_with_related
from spawns.text_output import render_event_text
from worlds.models import World


def _publish_state_sync(ctx: CommandContext, player_id: int) -> None:
    player = get_player_with_related(player_id)
    state = build_state_sync(player)
    payload = state.model_dump()
    text = render_event_text("cmd.state.sync.success", payload, viewer=player)
    publish_events(
        [
            GameEvent(
                type="cmd.state.sync.success",
                recipients=[player.key],
                data=payload,
                text=text,
            )
        ],
        actor_key=player.key,
        connection_id=ctx.connection_id,
    )


def _instance_ref_from_args(ctx: CommandContext) -> str | None:
    args = ctx.payload.get("args") or []
    if not args:
        return None
    ref = " ".join(str(arg) for arg in args).strip()
    return ref or None


@register_handler
class EnterInstanceHandler(CommandHandler):
    command_type = "enter"
    text_commands = ("enter",)
    help = {
        "name": "Enter Instance",
        "format": "enter [instance_ref]",
        "description": "Enter the instance linked from your current room.",
        "details": [
            "Use `enter` to start or re-enter your own run.",
            "Use `enter <instance_ref>` to join an existing active run for the same instance template.",
        ],
        "examples": [
            "enter",
            "enter 17d5144f5d4b4f34926aa4e32d9e40ea",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        player = Player.objects.select_related(
            "room",
            "room__transfer_to",
            "room__transfer_to__world",
            "world",
            "world__context",
        ).get(pk=ctx.player.id)

        if player.world.context and player.world.context.instance_of_id:
            ctx.publish_error("enter", "You are already in an instance. Use leave first.")
            return

        transfer_from = player.room
        if not transfer_from:
            ctx.publish_error("enter", "You are nowhere. Cannot enter an instance.")
            return

        transfer_to = transfer_from.transfer_to
        if not transfer_to:
            ctx.publish_error("enter", "There is no instance entrance here.")
            return

        instance_template = transfer_to.world
        if not instance_template.instance_of_id:
            ctx.publish_error("enter", "This room's instance link is not configured correctly.")
            return

        if player.world.context_id != instance_template.instance_of_id:
            ctx.publish_error("enter", "This instance entrance does not belong to this world.")
            return

        try:
            World.enter_instance(
                player=player,
                transfer_to_id=transfer_to.id,
                transfer_from_id=transfer_from.id,
                ref=_instance_ref_from_args(ctx),
            )
        except RuntimeError:
            ctx.publish_error("enter", "Invalid instance reference.")
            return

        _publish_state_sync(ctx, player.id)


@register_handler
class LeaveInstanceHandler(CommandHandler):
    command_type = "leave"
    text_commands = ("leave",)
    help = {
        "name": "Leave Instance",
        "format": "leave",
        "description": "Leave your current instance and return to its entrance room.",
        "examples": [
            "leave",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        player = Player.objects.select_related("world", "world__context").get(pk=ctx.player.id)
        if not player.world.context or not player.world.context.instance_of_id:
            ctx.publish_error("leave", "You are not in an instance.")
            return

        World.leave_instance(player=player)
        _publish_state_sync(ctx, player.id)


@register_handler
class InstanceInfoHandler(CommandHandler):
    command_type = "instance"
    text_commands = ("instance", "instances")
    help = {
        "name": "Instance",
        "format": "instance",
        "description": "Show the current instance or linked entrance information.",
        "examples": [
            "instance",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        player = Player.objects.select_related(
            "room",
            "room__transfer_to",
            "room__transfer_to__world",
            "world",
            "world__context",
            "world__context__instance_of",
        ).get(pk=ctx.player.id)

        if player.world.context and player.world.context.instance_of_id:
            ref = player.world.instance_ref or "unknown"
            template_name = player.world.context.name
            text = (
                "You are in %s.\n"
                "Instance ID: %s\n"
                "Use `leave` to return to %s."
            ) % (template_name, ref, player.world.context.instance_of.name)
            ctx.publish(
                {
                    "type": "cmd.instance.success",
                    "text": text,
                    "data": {
                        "status": "inside",
                        "instance_ref": player.world.instance_ref,
                        "template_world_id": player.world.context_id,
                        "base_world_id": player.world.context.instance_of_id,
                    },
                }
            )
            return

        transfer_to = player.room.transfer_to if player.room else None
        if transfer_to and transfer_to.world.instance_of_id:
            text = (
                "This room is linked to %s.\n"
                "Use `enter` to start or re-enter your run, or `enter <instance_ref>` to join another active run."
            ) % transfer_to.world.name
            ctx.publish(
                {
                    "type": "cmd.instance.success",
                    "text": text,
                    "data": {
                        "status": "entrance",
                        "template_world_id": transfer_to.world_id,
                        "entry_room_id": transfer_to.id,
                    },
                }
            )
            return

        ctx.publish(
            {
                "type": "cmd.instance.success",
                "text": "There is no instance entrance here.",
                "data": {"status": "none"},
            }
        )
