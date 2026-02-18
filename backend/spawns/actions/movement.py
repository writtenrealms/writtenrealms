from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from config import constants as adv_consts

from config import game_settings as adv_config
from spawns.actions.base import ActionError, ActionResult
from spawns.events import GameEvent
from spawns.models import Player
from spawns.state_payloads import (
    build_map_payload,
    collect_map_room_ids,
    door_state_lookup,
    get_player_with_related,
    safe_capitalize,
    serialize_actor,
    serialize_char_from_player,
    serialize_room,
)
from spawns.text_output import render_event_text
from worlds.models import Room


ROOM_COSTS = {
    adv_consts.ROOM_TYPE_ROAD: 1,
    adv_consts.ROOM_TYPE_CITY: 1,
    adv_consts.ROOM_TYPE_INDOOR: 1,
    adv_consts.ROOM_TYPE_FIELD: 2,
    adv_consts.ROOM_TYPE_TRAIL: 2,
    adv_consts.ROOM_TYPE_MOUNTAIN: 4,
    adv_consts.ROOM_TYPE_FOREST: 3,
    adv_consts.ROOM_TYPE_DESERT: 3,
    adv_consts.ROOM_TYPE_WATER: 3,
    adv_consts.ROOM_TYPE_SHALLOW: 3,
}


def _movement_cost(room: Room) -> int:
    return ROOM_COSTS.get(room.type, adv_config.MOVEMENT_COST)


def _room_with_exits(room_id: int) -> Room:
    return Room.objects.select_related(
        "north",
        "east",
        "south",
        "west",
        "up",
        "down",
        "zone",
        "world",
    ).get(pk=room_id)


@dataclass(frozen=True)
class MoveContext:
    player_id: int
    direction: str
    origin_room_id: int
    dest_room_id: int
    movement_cost: int


class ResolveMoveAction:
    def execute(self, player: Player, direction: str) -> ActionResult:
        if direction not in adv_consts.DIRECTIONS:
            raise ActionError("Unknown direction.", code="invalid_direction")

        if not player.room_id:
            raise ActionError("You are nowhere. Cannot move.", code="no_room")

        try:
            current_room = _room_with_exits(player.room_id)
        except Room.DoesNotExist:
            raise ActionError("Current room is invalid.", code="invalid_room")

        dest_room = getattr(current_room, direction, None)
        if not dest_room:
            raise ActionError("You cannot go that way.", code="no_exit")

        door_states = door_state_lookup(player.world, [current_room.id]).get(current_room.id, {})
        if door_states.get(direction) in ("closed", "locked"):
            raise ActionError("The way is blocked.", code="closed_door")

        movement_cost = _movement_cost(current_room)
        if player.stamina < movement_cost:
            raise ActionError("You are too exhausted to move.", code="exhausted")

        if dest_room.type == adv_consts.ROOM_TYPE_WATER:
            has_boat = player.inventory.filter(is_boat=True).exists()
            if not has_boat:
                raise ActionError(
                    "You'd need to know how to swim, or have a boat.",
                    code="water_room",
                )

        context = MoveContext(
            player_id=player.id,
            direction=direction,
            origin_room_id=current_room.id,
            dest_room_id=dest_room.id,
            movement_cost=movement_cost,
        )
        return ActionResult(data={"context": context})


class ChangeRoomAction:
    def execute(self, player: Player, dest_room_id: int) -> ActionResult:
        player.room_id = dest_room_id
        player.last_action_ts = timezone.now()
        return ActionResult(data={"dest_room_id": dest_room_id})


class AdjustStaminaAction:
    def execute(self, player: Player, delta: int) -> ActionResult:
        player.stamina = max(player.stamina + delta, 0)
        return ActionResult(data={"stamina_delta": delta})


class BuildMoveEventsAction:
    def execute(self, context: MoveContext) -> ActionResult:
        player = get_player_with_related(context.player_id)
        dest_room = _room_with_exits(context.dest_room_id)

        room_world = dest_room.world or (player.world.context or player.world)
        room_ids, _ = collect_map_room_ids(player, room_world, dest_room)
        door_states_all = door_state_lookup(player.world, room_ids)
        map_rooms, room_key_lookup = build_map_payload(room_world, room_ids, door_states_all)

        room_payload = serialize_room(
            dest_room,
            room_key_lookup,
            door_states_all,
            viewer=player,
        )
        actor_payload = serialize_actor(player, dest_room)

        door_state_updates = []
        for room_id, states in door_states_all.items():
            room_key = room_key_lookup.get(room_id)
            if not room_key:
                continue
            for dir_code, state in states.items():
                door_state_updates.append(
                    {"key": room_key, "direction": dir_code, "door_state": state}
                )

        move_data = {
            "direction": context.direction,
            "room": room_payload.model_dump(),
            "actor": actor_payload.model_dump(),
            "map": [room.model_dump() for room in map_rooms],
            "door_states": door_state_updates,
        }
        move_text = render_event_text("cmd.move.success", move_data, viewer=player)

        events: list[GameEvent] = [
            GameEvent(
                type="cmd.move.success",
                recipients=[player.key],
                data=move_data,
                text=move_text,
            )
        ]

        if not player.is_invisible:
            actor_char = serialize_char_from_player(player).model_dump()
            origin_recipients = (
                Player.objects.filter(
                    room_id=context.origin_room_id,
                    in_game=True,
                )
                .exclude(pk=player.id)
                .values_list("id", flat=True)
            )
            dest_recipients = (
                Player.objects.filter(
                    room_id=dest_room.id,
                    in_game=True,
                )
                .exclude(pk=player.id)
                .values_list("id", flat=True)
            )

            if origin_recipients:
                origin_keys = [f"player.{player_id}" for player_id in origin_recipients]
                events.append(
                    GameEvent(
                        type="notification.movement.exit",
                        recipients=origin_keys,
                        data={"actor": actor_char, "direction": context.direction},
                        text=f"{safe_capitalize(player.name)} leaves {context.direction}.",
                    )
                )

            if dest_recipients:
                dest_keys = [f"player.{player_id}" for player_id in dest_recipients]
                rev_dir = adv_consts.REVERSE_DIRECTIONS[context.direction]
                if rev_dir == "up":
                    rev_text = "above"
                elif rev_dir == "down":
                    rev_text = "below"
                else:
                    rev_text = f"the {rev_dir}"
                events.append(
                    GameEvent(
                        type="notification.movement.enter",
                        recipients=dest_keys,
                        data={"actor": actor_char, "direction": rev_dir},
                        text=f"{safe_capitalize(player.name)} has arrived from {rev_text}.",
                    )
                )

        return ActionResult(events=events)
