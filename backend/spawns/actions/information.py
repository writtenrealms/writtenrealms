from __future__ import annotations

from core.utils import roll_die
from spawns.actions.base import ActionError, ActionResult
from spawns.events import GameEvent
from spawns.models import Player
from spawns.state_payloads import (
    build_map_payload,
    collect_map_room_ids,
    door_state_lookup,
    get_player_with_related,
    serialize_actor,
    serialize_char_from_player,
    serialize_room,
)
from spawns.text_output import render_event_text


def _normalize_roll_target(target: str | None) -> str:
    normalized_target = str(target).strip() if target is not None else "6"
    if not normalized_target:
        normalized_target = "6"
    if "d" not in normalized_target:
        return f"1d{normalized_target}"
    return normalized_target


class LookAction:
    def execute(self, player_id: int) -> ActionResult:
        player = get_player_with_related(player_id)
        world = player.world
        room = player.room

        if room is None:
            raise ActionError("You are nowhere. Cannot look around.", code="no_room")

        room_world = room.world or (world.context or world)
        room_ids, _ = collect_map_room_ids(player, room_world, room)
        door_states = door_state_lookup(world, room_ids)
        map_rooms, room_key_lookup = build_map_payload(room_world, room_ids, door_states)

        room_payload = serialize_room(
            room,
            room_key_lookup,
            door_states,
            viewer=player,
        )
        actor_payload = serialize_actor(player, room)
        data = {
            "actor": actor_payload.model_dump(),
            "target": room_payload.model_dump(),
            "target_type": "room",
            "map": [mr.model_dump() for mr in map_rooms],
        }
        text = render_event_text("cmd.look.success", data, viewer=player)

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.look.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ]
        )


class InventoryAction:
    def execute(self, player_id: int) -> ActionResult:
        player = get_player_with_related(player_id)
        actor_payload = serialize_actor(player, player.room)
        data = {"actor": actor_payload.model_dump()}
        text = render_event_text("cmd.inventory.success", data, viewer=player)

        return ActionResult(
            events=[
                GameEvent(
                    type="cmd.inventory.success",
                    recipients=[player.key],
                    data=data,
                    text=text,
                )
            ]
        )


class RollAction:
    def execute(self, player_id: int, target: str | None = None) -> ActionResult:
        player = get_player_with_related(player_id)
        die = _normalize_roll_target(target)
        outcome = roll_die(die)

        data = {
            "die": die,
            "outcome": outcome,
        }

        cmd_text = render_event_text("cmd.roll.success", data, viewer=player)
        events = [
            GameEvent(
                type="cmd.roll.success",
                recipients=[player.key],
                data=data,
                text=cmd_text,
            )
        ]

        if player.room_id and not player.is_invisible:
            recipient_ids = list(
                Player.objects.filter(
                    room_id=player.room_id,
                    in_game=True,
                )
                .exclude(pk=player.id)
                .values_list("id", flat=True)
            )
            if recipient_ids:
                notify_data = {
                    "actor": serialize_char_from_player(player).model_dump(),
                    "die": die,
                    "outcome": outcome,
                }
                notify_text = render_event_text(
                    "notification.cmd.roll.success",
                    notify_data,
                    viewer=None,
                )
                events.append(
                    GameEvent(
                        type="notification.cmd.roll.success",
                        recipients=[f"player.{recipient_id}" for recipient_id in recipient_ids],
                        data=notify_data,
                        text=notify_text,
                    )
                )

        return ActionResult(events=events)
