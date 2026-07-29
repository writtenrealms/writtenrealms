"""
Movement command handler.

Command -> Action -> Event:
- Command orchestrates multiple Actions
- Actions mutate state and/or build events
- Handler publishes events
"""
import logging

from django.db import transaction

from config import constants as adv_consts
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
from spawns.triggers import evaluate_movement_policies


logger = logging.getLogger(__name__)


@register_handler
class MoveHandler(CommandHandler):
    command_type = "move"
    text_commands = ("north", "east", "south", "west", "up", "down")
    text_aliases = {
        "n": adv_consts.DIRECTION_NORTH,
        "e": adv_consts.DIRECTION_EAST,
        "s": adv_consts.DIRECTION_SOUTH,
        "w": adv_consts.DIRECTION_WEST,
        "u": adv_consts.DIRECTION_UP,
        "d": adv_consts.DIRECTION_DOWN,
    }
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
        tracker_plan = None
        tracker_room_snapshot = None
        followup_events = []
        move_events_published = False

        try:
            with transaction.atomic():
                player = (
                    Player.objects.select_for_update(of=("self",))
                    .select_related("world")
                    .get(pk=ctx.player.id)
                )

                from spawns.actions.mob_movement import (
                    ResolveTrackerChaseAction,
                    load_player_escape_encounters,
                    plan_player_escape,
                )

                escape_encounters = load_player_escape_encounters(
                    player=player,
                    origin_room_id=player.room_id,
                )
                from spawns.actions.pvp import active_pvp_participation

                pvp_participation = active_pvp_participation(
                    player,
                    room=player.room,
                )
                if any(
                    encounter.is_combat_locked
                    for encounter in escape_encounters
                ) or (
                    pvp_participation is not None
                    and pvp_participation.encounter.is_combat_locked
                ):
                    raise ActionError(
                        "You are locked in combat. You must flee.",
                        code="in_combat",
                    )

                from spawns.actions.doors import (
                    cancel_pending_player_door_action,
                )

                followup_events.extend(
                    cancel_pending_player_door_action(
                        player=player,
                        code="actor_moved",
                        message="You stop working with the door as you move.",
                    )
                )
                resolution = ResolveMoveAction().execute(player, direction)
                context = resolution.data["context"]

                tracker_plan = plan_player_escape(
                    player=player,
                    origin_room_id=context.origin_room_id,
                    destination_room_id=context.dest_room_id,
                    direction=context.direction,
                    source="move",
                    encounters=escape_encounters,
                )

                for policy_event in (
                    adv_consts.TRIGGER_EVENT_BEFORE_MOVE_EXIT,
                    adv_consts.TRIGGER_EVENT_BEFORE_MOVE_ENTER,
                ):
                    policy_result = evaluate_movement_policies(
                        actor=player,
                        event=policy_event,
                        direction=context.direction,
                        origin_room_id=context.origin_room_id,
                        destination_room_id=context.dest_room_id,
                        world_id=context.trigger_world_id,
                    )
                    if not policy_result.allowed:
                        raise ActionError(
                            policy_result.feedback or "You cannot go that way.",
                            code=policy_result.code,
                            data={"trigger_id": policy_result.trigger_id},
                        )

                ChangeRoomAction().execute(player, context.dest_room_id)
                AdjustStaminaAction().execute(player, -context.movement_cost)

                player.save(update_fields=["room", "stamina", "last_action_ts"])
                player.viewed_rooms.add(context.dest_room_id)

                if pvp_participation is not None:
                    pvp_encounter_id = pvp_participation.encounter_id

                    def _finish_pvp_after_move() -> None:
                        from spawns.actions.pvp import finish_pvp_encounter

                        resolved_events = finish_pvp_encounter(
                            pvp_encounter_id,
                        )
                        if not resolved_events:
                            return
                        if move_events_published:
                            publish_events(
                                resolved_events,
                                actor_key=ctx.player.key,
                                connection_id=ctx.connection_id,
                            )
                        else:
                            followup_events.extend(resolved_events)

                    # Preserve the global duel lock order by cleaning up only
                    # after this player-owned movement transaction commits.
                    transaction.on_commit(
                        _finish_pvp_after_move,
                        robust=True,
                    )

                tracker_payload = (
                    tracker_plan.action_payload()
                    if tracker_plan.encounter_ids
                    else None
                )

                def _resolve_tracker_chase() -> None:
                    nonlocal tracker_room_snapshot
                    resolved_events = []
                    try:
                        tracker_result = ResolveTrackerChaseAction().execute(
                            **tracker_payload
                        )
                    except Exception:
                        logger.exception(
                            "Failed to resolve tracker chase %s.",
                            tracker_plan.chase_key,
                        )
                        from spawns.ability_prepare_state import (
                            ability_prepare_state_events_for_players,
                        )

                        resolved_events.extend(
                            ability_prepare_state_events_for_players(
                                [ctx.player.id]
                            )
                        )
                    else:
                        resolved_events.extend(tracker_result.events)
                        snapshot = tracker_result.data.get(
                            "destination_room_snapshot"
                        )
                        if isinstance(snapshot, dict):
                            tracker_room_snapshot = snapshot

                    if not resolved_events:
                        return
                    if move_events_published:
                        publish_events(
                            resolved_events,
                            actor_key=ctx.player.key,
                            connection_id=ctx.connection_id,
                        )
                    else:
                        followup_events.extend(resolved_events)

                # Register before the move commits so combat cleanup and chase
                # cannot be skipped by later event construction/publication.
                if tracker_payload:
                    transaction.on_commit(_resolve_tracker_chase)

            events_result = BuildMoveEventsAction().execute(
                context,
                room_payload_override=tracker_room_snapshot,
            )

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
        move_events_published = True
        if followup_events:
            publish_events(
                followup_events,
                actor_key=ctx.player.key,
                connection_id=ctx.connection_id,
            )

        from spawns.actions.combat import ScanRoomAggroAction

        aggro_result = ScanRoomAggroAction().execute(ctx.player.id)
        if aggro_result.events:
            publish_events(
                aggro_result.events,
                actor_key=ctx.player.key,
                connection_id=ctx.connection_id,
            )
