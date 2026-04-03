from __future__ import annotations

from django.db import transaction

from spawns.actions.base import ActionError, ActionResult
from spawns.actions.targeting import resolve_room_mob_target
from spawns.events import GameEvent
from spawns.models import Player
from spawns.state_payloads import (
    get_player_with_related,
    room_payload_key_for,
    serialize_actor,
    serialize_char_from_mob,
    serialize_char_from_player,
    serialize_room,
)
from spawns.text_output import render_event_text
from worlds.models import Room


class KillAction:
    def execute(self, player_id: int, target_selector: str | None) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot kill anything.", code="no_room")

            room = Room.objects.get(pk=player.room_id)
            target_mob = resolve_room_mob_target(
                room,
                target_selector,
                empty_error="Kill what?",
                not_found_error="You don't see them here.",
            )
            target_payload = serialize_char_from_mob(target_mob).model_dump()
            target_mob.delete()

        updated_player = get_player_with_related(player_id)
        actor_payload = serialize_actor(updated_player, updated_player.room)
        room_payload = serialize_room(
            room,
            {room.id: room_payload_key_for(room)},
            {},
            viewer=updated_player,
        )
        data = {
            "actor": actor_payload.model_dump(),
            "target": target_payload,
            "room": room_payload.model_dump(),
        }
        text = render_event_text("cmd.kill.success", data, viewer=updated_player)

        events = [
            GameEvent(
                type="cmd.kill.success",
                recipients=[updated_player.key],
                data=data,
                text=text,
            ),
            GameEvent(
                type="quest.mob.killed",
                recipients=[],
                data=data,
            ),
        ]

        if not updated_player.is_invisible:
            recipients = (
                Player.objects.filter(room_id=room.id, in_game=True)
                .exclude(pk=updated_player.id)
                .values_list("id", flat=True)
            )
            if recipients:
                notify_data = {
                    "actor": serialize_char_from_player(updated_player).model_dump(),
                    "target": target_payload,
                }
                notify_text = render_event_text(
                    "notification.cmd.kill.success",
                    notify_data,
                    viewer=None,
                )
                events.append(
                    GameEvent(
                        type="notification.cmd.kill.success",
                        recipients=[f"player.{pid}" for pid in recipients],
                        data=notify_data,
                        text=notify_text,
                    )
                )

        return ActionResult(events=events)
