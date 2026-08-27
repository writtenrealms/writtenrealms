"""
Movement command handler.

Command -> Action -> Event:
- Command orchestrates multiple Actions
- Actions mutate state and/or build events
- Handler publishes events
"""
import logging

from django.db import OperationalError, transaction

from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.actions.movement import (
    AdjustStaminaAction,
    BuildMoveEventsAction,
    ChangeRoomAction,
    MoveMobAction,
    ResolveMoveAction,
)
from spawns.events import (
    FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE,
    FOLLOW_HAS_FOLLOWERS_KEY,
    durable_follow_directional_move_event,
    follow_directional_move_event,
    persist_follow_dependent_game_events,
    publish_events,
)
from spawns.handlers.base import (
    TRIGGER_STEP_MODE_TRANSACTIONAL,
    CommandContext,
    CommandHandler,
)
from spawns.handlers.permissions import has_builder_access
from spawns.handlers.registry import register_handler
from spawns.models import Player
from spawns.triggers import evaluate_movement_policies


logger = logging.getLogger(__name__)


@register_handler
class MoveHandler(CommandHandler):
    command_type = "move"
    supported_actor_types = ("player", "mob")
    trigger_step_mode = TRIGGER_STEP_MODE_TRANSACTIONAL
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

    def validate_trigger_step_command(
        self,
        *,
        command: str,
        subject_type: str,
        subject_key: str,
        render_actor_key: str,
    ) -> tuple[str, str] | None:
        if subject_type not in {"player", "mob"}:
            return (
                "Only player or mob subjects may execute movement commands "
                "in Trigger steps.",
                "command_not_step_safe",
            )
        if subject_type == "player" and (
            not str(render_actor_key or "").lower().startswith("player.")
            or str(subject_key or "").lower()
            != str(render_actor_key or "").lower()
        ):
            return (
                "Player movement commands require the player Trigger actor.",
                "unsupported_command_subject",
            )
        if len(str(command or "").split()) != 1:
            return (
                "A movement action must contain one bare direction.",
                "invalid_args",
            )
        return None

    def _handle_mob(self, ctx: CommandContext, direction: str) -> None:
        audited_trigger_step = bool(
            ctx.trigger_step and ctx.capture_only and ctx.script_source
        )
        authorized_builder_force = bool(
            ctx.builder_force
            and not ctx.script_source
            and ctx.issuer_type == "player"
            and isinstance(ctx.issuer, Player)
            and has_builder_access(ctx.issuer)
        )
        if not (audited_trigger_step or authorized_builder_force):
            message = (
                "Mob movement is supported only by audited Trigger steps "
                "or authorized builder forcing."
            )
            ctx.publish(
                {
                    "type": "cmd.move.error",
                    "text": message,
                    "data": {
                        "error": message,
                        "code": "command_not_step_safe",
                    },
                }
            )
            return
        try:
            result = MoveMobAction().execute(
                mob_id=ctx.mob.id,
                direction=direction,
                runtime_world=ctx.world,
                trigger_step=ctx.trigger_step,
                connection_id=ctx.connection_id,
            )
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.move.error",
                    "text": err.message,
                    "data": {
                        "error": err.message,
                        "code": err.code,
                        **err.data,
                    },
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.mob.key,
            connection_id=ctx.connection_id,
        )

    def handle(self, ctx: CommandContext) -> None:
        from spawns.following import (
            FOLLOW_MOVEMENT_PAYLOAD_KEY,
            FollowMovementDeferred,
        )

        direction = ctx.payload.get("direction")
        if ctx.mob is not None:
            self._handle_mob(ctx, direction)
            return
        follow_context = ctx.payload.get(FOLLOW_MOVEMENT_PAYLOAD_KEY)
        if not isinstance(follow_context, dict):
            follow_context = None
        follow_relationship = None
        tracker_plan = None
        tracker_room_snapshot = None
        followup_events = []
        move_events_published = False
        move_events_queued = False
        durable_follow_event = None
        events_result = None
        post_move_callbacks = []

        try:
            with transaction.atomic():
                try:
                    player = (
                        Player.objects.select_for_update(
                            of=("self",),
                            nowait=follow_context is not None,
                        )
                        .select_related("world")
                        .get(pk=ctx.player.id)
                    )
                except OperationalError as exc:
                    if follow_context is not None:
                        raise FollowMovementDeferred() from exc
                    raise
                if follow_context is not None:
                    from spawns.following import (
                        complete_follow_movement_attempt,
                        lock_follow_movement_attempt,
                        set_follow_movement_result,
                    )

                    follow_relationship, normalized_follow = (
                        lock_follow_movement_attempt(
                            player=player,
                            context=follow_context,
                        )
                    )
                    if (
                        normalized_follow["actor_is_invisible"]
                        and not player.is_builder
                    ):
                        # Consume the snapshotted concealed edge atomically,
                        # without moving or emitting anything that reveals the
                        # leader's route. The relationship remains available
                        # for a later visible, co-located movement.
                        complete_follow_movement_attempt(
                            follow_relationship,
                            sequence=normalized_follow["sequence"],
                        )
                        set_follow_movement_result(
                            follow_context,
                            "concealed",
                        )
                        return
                else:
                    normalized_follow = None

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
                resolution = ResolveMoveAction().execute(
                    player,
                    direction,
                    source="follow" if normalized_follow else "move",
                    follow_root_id=(
                        normalized_follow["root_id"]
                        if normalized_follow
                        else None
                    ),
                    follow_depth=(
                        normalized_follow["depth"] + 1
                        if normalized_follow
                        else 0
                    ),
                )
                context = resolution.data["context"]
                if (
                    normalized_follow is not None
                    and context.dest_room_id
                    != normalized_follow["destination_room_id"]
                ):
                    raise ActionError(
                        "Your leader did not take that exit.",
                        code="follow_destination_changed",
                    )

                tracker_plan = plan_player_escape(
                    player=player,
                    origin_room_id=context.origin_room_id,
                    destination_room_id=context.dest_room_id,
                    direction=context.direction,
                    source=context.source,
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

                player.save(
                    update_fields=[
                        "room",
                        "location_sequence",
                        "follow_move_sequence",
                        "stamina",
                        "last_action_ts",
                    ]
                )
                player.viewed_rooms.add(context.dest_room_id)
                durable_follow_event = durable_follow_directional_move_event(
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
                if follow_relationship is not None:
                    from spawns.following import complete_follow_movement_attempt

                    complete_follow_movement_attempt(
                        follow_relationship,
                        sequence=normalized_follow["sequence"],
                    )

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
                    post_move_callbacks.append(
                        (_finish_pvp_after_move, True)
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

                if tracker_payload:
                    post_move_callbacks.append(
                        (_resolve_tracker_chase, False)
                    )

                requires_durable_publication = bool(
                    follow_context is not None
                    or durable_follow_event.data.get(
                        FOLLOW_HAS_FOLLOWERS_KEY
                    )
                )
                if requires_durable_publication:
                    # Build the visible movement and structural room-entry
                    # events while the room mutation is still transactional.
                    # Ordinary movement persists them here before a follow
                    # edge can be acknowledged. An audited Trigger runner must
                    # capture them instead so the enclosing step owns final
                    # provenance, ordering, publication, and rollback.
                    events_result = BuildMoveEventsAction().execute(
                        context,
                        follow_event_override=durable_follow_event,
                    )
                    audited_trigger_step = bool(
                        ctx.trigger_step
                        and ctx.capture_only
                        and ctx.script_source
                    )
                    if not audited_trigger_step:
                        persisted_events = (
                            persist_follow_dependent_game_events(
                                [*events_result.events, *followup_events],
                                force=follow_context is not None,
                                actor_key=ctx.player.key,
                                connection_id=ctx.connection_id,
                            )
                        )
                        if persisted_events:
                            raise RuntimeError(
                                "Required movement events were not persisted."
                            )
                        followup_events.clear()
                        move_events_queued = True
                        # Post-commit duel/tracker callbacks are registered after
                        # the batch-specific fast path and publish their updates
                        # after the pre-chase movement snapshot.
                        move_events_published = True

                for callback, robust in post_move_callbacks:
                    if robust:
                        transaction.on_commit(callback, robust=True)
                    else:
                        transaction.on_commit(callback)

            if events_result is None:
                events_result = BuildMoveEventsAction().execute(
                    context,
                    room_payload_override=tracker_room_snapshot,
                    follow_event_override=durable_follow_event,
                )

        except Exception as exc:
            from spawns.following import (
                FOLLOW_FAILURE_DEFERRED,
                FOLLOW_FAILURE_IGNORED,
                FollowMovementBlocked,
                FollowMovementDeferred,
                FollowMovementIgnored,
                follow_failure_message,
                record_failed_follow_movement,
                set_follow_movement_result,
            )

            if isinstance(exc, FollowMovementIgnored):
                set_follow_movement_result(follow_context, "ignored")
                return
            if isinstance(exc, FollowMovementDeferred):
                set_follow_movement_result(follow_context, "deferred")
                return
            if isinstance(exc, FollowMovementBlocked):
                failure_result = record_failed_follow_movement(
                    player_id=ctx.player.id,
                    context=follow_context,
                    message=exc.message,
                    code=exc.code,
                    connection_id=ctx.connection_id,
                )
                if failure_result == FOLLOW_FAILURE_DEFERRED:
                    set_follow_movement_result(follow_context, "deferred")
                    return
                if failure_result == FOLLOW_FAILURE_IGNORED:
                    set_follow_movement_result(follow_context, "ignored")
                    return
                set_follow_movement_result(follow_context, "blocked")
                return
            if not isinstance(exc, ActionError):
                raise

            err = exc
            if follow_context is not None:
                error_text = follow_failure_message(
                    follow_context,
                    err.message,
                )
                failure_result = record_failed_follow_movement(
                    player_id=ctx.player.id,
                    context=follow_context,
                    message=error_text,
                    code=err.code,
                    data=err.data,
                    connection_id=ctx.connection_id,
                )
                if failure_result == FOLLOW_FAILURE_DEFERRED:
                    set_follow_movement_result(follow_context, "deferred")
                    return
                if failure_result == FOLLOW_FAILURE_IGNORED:
                    set_follow_movement_result(follow_context, "ignored")
                    return
                set_follow_movement_result(follow_context, "blocked")
            else:
                error_text = err.message
                ctx.publish(
                    {
                        "type": "cmd.move.error",
                        "text": error_text,
                        "data": {
                            "error": error_text,
                            "code": err.code,
                            **err.data,
                        },
                    }
                )
            return

        movement_events = []
        if not move_events_queued:
            movement_events = [
                event
                for event in events_result.events
                if event.type == FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
            ]
            visible_move_events = [
                event
                for event in events_result.events
                if event.type != FOLLOW_DIRECTIONAL_MOVE_EVENT_TYPE
            ]
            publish_events(
                visible_move_events,
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
        if movement_events:
            publish_events(
                movement_events,
                actor_key=ctx.player.key,
                connection_id=ctx.connection_id,
            )
