from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from config import constants as adv_consts

from spawns.actions.base import ActionError, ActionResult
from spawns.actions.movement_costs import movement_cost
from spawns.events import (
    GameEvent,
    durable_follow_directional_move_event,
    follow_directional_move_event,
    persist_follow_dependent_game_events,
    player_room_enter_event,
)
from spawns.models import CombatEncounter, Mob, Player
from spawns.state_payloads import (
    build_map_payload,
    collect_map_room_ids,
    door_state_lookup,
    get_player_with_related,
    safe_capitalize,
    serialize_actor,
    serialize_char_from_mob,
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


def _mob_arrival_source_text(reverse_direction: str) -> str:
    if reverse_direction == adv_consts.DIRECTION_UP:
        return "above"
    if reverse_direction == adv_consts.DIRECTION_DOWN:
        return "below"
    return f"the {reverse_direction}"


def _mob_directional_move_events(
    *,
    mob: Mob,
    origin_room_id: int,
    destination_room_id: int,
    direction: str,
) -> list[GameEvent]:
    events: list[GameEvent] = []
    if not mob.is_invisible:
        actor_payload = serialize_char_from_mob(mob).model_dump()
        actor_name = safe_capitalize(actor_payload.get("name") or "Someone")
        recipient_ids_by_room = {
            origin_room_id: [],
            destination_room_id: [],
        }
        for room_id, player_id in Player.objects.filter(
            world_id=mob.world_id,
            room_id__in=recipient_ids_by_room,
            in_game=True,
        ).values_list("room_id", "id"):
            recipient_ids_by_room[room_id].append(player_id)
        origin_recipients = tuple(
            f"player.{player_id}"
            for player_id in recipient_ids_by_room[origin_room_id]
        )
        if origin_recipients:
            events.append(
                GameEvent(
                    type="notification.movement.exit",
                    recipients=origin_recipients,
                    data={"actor": actor_payload, "direction": direction},
                    text=f"{actor_name} leaves {direction}.",
                )
            )

        destination_recipients = tuple(
            f"player.{player_id}"
            for player_id in recipient_ids_by_room[destination_room_id]
        )
        if destination_recipients:
            reverse_direction = adv_consts.REVERSE_DIRECTIONS[direction]
            events.append(
                GameEvent(
                    type="notification.movement.enter",
                    recipients=destination_recipients,
                    data={
                        "actor": actor_payload,
                        "direction": reverse_direction,
                    },
                    text=(
                        f"{actor_name} has arrived from "
                        f"{_mob_arrival_source_text(reverse_direction)}."
                    ),
                )
            )

    events.append(
        follow_directional_move_event(
            actor=mob,
            origin_room_id=origin_room_id,
            destination_room_id=destination_room_id,
            direction=direction,
            source="move",
        )
    )
    return events


class MoveMobAction:
    """Move one live mob through an adjacent exit as an embodied command."""

    @staticmethod
    def _authored_world_id(runtime_world) -> int:
        return runtime_world.context_id or runtime_world.id

    def execute(
        self,
        *,
        mob_id: int,
        direction: str,
        runtime_world,
        trigger_step: bool = False,
        connection_id: str | None = None,
    ) -> ActionResult:
        normalized_direction = str(direction or "").strip().lower()
        if normalized_direction not in adv_consts.DIRECTIONS:
            raise ActionError("Unknown direction.", code="invalid_direction")
        if runtime_world is None:
            raise ActionError(
                "No runtime world is available for movement.",
                code="no_world",
            )

        with transaction.atomic():
            mob = (
                Mob.objects.select_for_update(of=("self",))
                .select_related("definition", "room", "world")
                .filter(
                    pk=mob_id,
                    world_id=runtime_world.id,
                    is_pending_deletion=False,
                )
                .first()
            )
            if mob is None:
                raise ActionError(
                    "The mob is no longer available.",
                    code="actor_missing",
                )
            if int(mob.health or 0) <= 0:
                raise ActionError(
                    "The mob is unable to move.",
                    code="actor_unavailable",
                )
            if not mob.room_id:
                raise ActionError(
                    "The mob is nowhere and cannot move.",
                    code="no_room",
                )

            authored_world_id = self._authored_world_id(runtime_world)
            try:
                current_room = _room_with_exits(mob.room_id)
            except Room.DoesNotExist as exc:
                raise ActionError(
                    "The mob's current room is invalid.",
                    code="invalid_room",
                ) from exc
            if current_room.world_id != authored_world_id:
                raise ActionError(
                    "The mob is outside this runtime world's authored rooms.",
                    code="invalid_world_context",
                )
            destination = getattr(current_room, normalized_direction, None)
            if destination is None:
                raise ActionError(
                    "You cannot go that way.",
                    code="no_exit",
                )
            if destination.world_id != authored_world_id:
                raise ActionError(
                    "The destination is outside this runtime world.",
                    code="invalid_world_context",
                )
            if CombatEncounter.objects.filter(
                mob_id=mob.id,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists():
                raise ActionError(
                    "The mob is in combat and cannot move.",
                    code="in_combat",
                )

            from spawns.actions.doors import lock_door_state_for_movement

            door_state = lock_door_state_for_movement(
                runtime_world=runtime_world,
                room_id=current_room.id,
                direction=normalized_direction,
            )
            if door_state and door_state.state in (
                adv_consts.DOOR_STATE_CLOSED,
                adv_consts.DOOR_STATE_LOCKED,
            ):
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

            origin_room_id = current_room.id
            mob.room = destination
            mob.follow_move_sequence = int(mob.follow_move_sequence or 0) + 1
            mob.save(
                update_fields=[
                    "room",
                    "follow_move_sequence",
                    "modified_ts",
                ]
            )
            events = _mob_directional_move_events(
                mob=mob,
                origin_room_id=origin_room_id,
                destination_room_id=destination.id,
                direction=normalized_direction,
            )
            # Snapshot follower presence at this action's authored position.
            # A later same-step ``follow`` must not retroactively join an edge
            # that already happened. The surrounding Trigger transaction still
            # owns rollback and final visible-event ordering.
            events[-1] = durable_follow_directional_move_event(events[-1])
            if not trigger_step:
                events = persist_follow_dependent_game_events(
                    events,
                    actor_key=mob.key,
                    connection_id=connection_id,
                )

        return ActionResult(
            events=events,
            data={
                "mob_id": mob.id,
                "origin_room_id": origin_room_id,
                "destination_room_id": destination.id,
                "direction": normalized_direction,
            },
        )


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
