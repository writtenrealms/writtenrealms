from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from config import constants as adv_consts

from spawns.actions.base import ActionError, ActionResult
from spawns.actions.movement_costs import movement_cost
from spawns.events import (
    GameEvent,
    durable_follow_directional_move_event,
    follow_directional_move_event,
    player_room_enter_event,
)
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


_movement_cost = movement_cost


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
    trigger_world_id: int
    movement_cost: int
    source: str = "move"
    follow_root_id: str | None = None
    follow_depth: int = 0


class ResolveMoveAction:
    def execute(
        self,
        player: Player,
        direction: str,
        *,
        source: str = "move",
        follow_root_id: str | None = None,
        follow_depth: int = 0,
    ) -> ActionResult:
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

        from spawns.actions.doors import lock_door_state_for_movement

        door_state = lock_door_state_for_movement(
            runtime_world=player.world,
            room_id=current_room.id,
            direction=direction,
        )
        if door_state and door_state.state in ("closed", "locked"):
            door_name = door_state.face.name or "door"
            raise ActionError(
                f"The {door_name} is {door_state.state}.",
                code="closed_door",
                data={
                    "door": {
                        "key": f"door.{door_state.face.id}",
                        "name": door_name,
                        "direction": door_state.face.direction,
                        "state": door_state.state,
                    },
                },
            )

        movement_cost = _movement_cost(dest_room)
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
            trigger_world_id=dest_room.world_id,
            movement_cost=movement_cost,
            source=str(source or "move"),
            follow_root_id=follow_root_id,
            follow_depth=max(0, int(follow_depth or 0)),
        )
        return ActionResult(data={"context": context})


class ChangeRoomAction:
    def execute(self, player: Player, dest_room_id: int) -> ActionResult:
        if player.room_id != dest_room_id:
            player.room_id = dest_room_id
            player.location_sequence = int(player.location_sequence or 0) + 1
            player.follow_move_sequence = (
                int(player.follow_move_sequence or 0) + 1
            )
        player.last_action_ts = timezone.now()
        return ActionResult(data={"dest_room_id": dest_room_id})


class AdjustStaminaAction:
    def execute(self, player: Player, delta: int) -> ActionResult:
        player.stamina = max(player.stamina + delta, 0)
        return ActionResult(data={"stamina_delta": delta})


def _room_ref_payload(room: Room | None, fallback_id: int) -> dict:
    payload = {"id": fallback_id}
    if room:
        payload["key"] = room.key
        payload["name"] = room.name or ""
    return payload


class BuildMoveEventsAction:
    def execute(
        self,
        context: MoveContext,
        *,
        room_payload_override: dict | None = None,
        follow_event_override: GameEvent | None = None,
    ) -> ActionResult:
        player = get_player_with_related(context.player_id)
        dest_room = _room_with_exits(context.dest_room_id)
        origin_room = Room.objects.filter(pk=context.origin_room_id).only(
            "id",
            "relative_id",
            "name",
        ).first()

        room_world = dest_room.world or (player.world.context or player.world)
        room_ids, _ = collect_map_room_ids(player, room_world, dest_room)
        door_states_all = door_state_lookup(player.world, room_ids)
        map_rooms, room_key_lookup = build_map_payload(room_world, room_ids, door_states_all)

        room_payload = room_payload_override
        if room_payload is None:
            room_payload = serialize_room(
                dest_room,
                room_key_lookup,
                door_states_all,
                viewer=player,
                runtime_world=player.world,
            ).model_dump()
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
            "room": room_payload,
            "origin_room": _room_ref_payload(origin_room, context.origin_room_id),
            "destination_room": _room_ref_payload(dest_room, context.dest_room_id),
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
        if context.origin_room_id != context.dest_room_id:
            events.append(player_room_enter_event(
                player=player,
                origin_room_id=context.origin_room_id,
                destination_room_id=context.dest_room_id,
                source=context.source,
                direction=context.direction,
            ))

        if not player.is_invisible:
            actor_char = serialize_char_from_player(player).model_dump()
            origin_recipients = (
                Player.objects.filter(
                    world=player.world,
                    room_id=context.origin_room_id,
                    in_game=True,
                )
                .exclude(pk=player.id)
                .values_list("id", flat=True)
            )
            dest_recipients = (
                Player.objects.filter(
                    world=player.world,
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

        if context.origin_room_id != context.dest_room_id:
            events.append(
                follow_event_override
                or durable_follow_directional_move_event(
                    follow_directional_move_event(
                        actor=player,
                        origin_room_id=context.origin_room_id,
                        destination_room_id=context.dest_room_id,
                        direction=context.direction,
                        source=context.source,
                        root_id=context.follow_root_id,
                        depth=context.follow_depth,
                    )
                )
            )

        return ActionResult(events=events)
