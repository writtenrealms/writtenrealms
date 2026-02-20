"""
Movement command handler.

Command -> Action -> Event:
- Command orchestrates multiple Actions
- Actions mutate state and/or build events
- Handler publishes events
"""
from django.db import transaction

from spawns.actions.base import ActionError
from spawns.actions.movement import (
    AdjustStaminaAction,
    BuildMoveEventsAction,
    ChangeRoomAction,
    ResolveMoveAction,
)
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler
from spawns.models import Player


@register_handler
class MoveHandler(CommandHandler):
    command_type = "move"
    text_commands = ("north", "east", "south", "west", "up", "down")
    help = {
        "name": "Move",
        "format": "north | east | south | west | up | down",
        "description": "Move to an adjacent room in the given direction.",
        "examples": [
            "north",
            "e",
            "down",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        direction = ctx.payload.get("direction")

        try:
            with transaction.atomic():
                player = Player.objects.select_for_update().get(pk=ctx.player.id)

                resolution = ResolveMoveAction().execute(player, direction)
                context = resolution.data["context"]

                ChangeRoomAction().execute(player, context.dest_room_id)
                AdjustStaminaAction().execute(player, -context.movement_cost)

                player.save(update_fields=["room", "stamina", "last_action_ts"])
                player.viewed_rooms.add(context.dest_room_id)

            events_result = BuildMoveEventsAction().execute(context)

        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.move.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            events_result.events,
            actor_key=ctx.player.key,
            connection_id=ctx.connection_id,
        )
