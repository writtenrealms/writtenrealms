from __future__ import annotations

from contextlib import contextmanager
from typing import Callable
import uuid

from django.db import transaction

from config import constants as adv_consts
from spawns.events import (
    FINAL_TRANSFER_ENTER_KEY,
    TRANSFER_LOCATION_SEQUENCE_KEY,
    TRANSFER_RUNTIME_WORLD_KEY,
    capture_game_events,
    enqueue_game_events,
    flush_game_event_outbox,
)
from spawns.models import EventSubscriptionReceipt, Mob, Player


def execute_mob_event_triggers(*args, **kwargs):
    from spawns.triggers import execute_mob_event_triggers as execute

    return execute(*args, **kwargs)


def execute_room_event_triggers(*args, **kwargs):
    from spawns.triggers import execute_room_event_triggers as execute

    return execute(*args, **kwargs)


TriggerSubscriptionHandler = Callable[[dict, str | None, str | None], None]


@contextmanager
def _release_trigger_gates_on_error(
    claims: list[tuple[str, str]],
):
    try:
        yield
    except Exception:
        from spawns.trigger_steps import release_trigger_gate_claims

        release_trigger_gate_claims(claims)
        raise


def _extract_actor_key(event_data: dict, actor_key: str | None) -> str | None:
    actor = event_data.get("actor")
    if isinstance(actor, dict):
        actor_ref = actor.get("key")
        if actor_ref:
            return str(actor_ref)
    return actor_key


def _resolve_player(actor_ref: str | None) -> Player | None:
    if not actor_ref:
        return None
    actor_ref = str(actor_ref)
    if not actor_ref.startswith("player."):
        return None

    player_id_text = actor_ref.split(".", 1)[1]
    if not player_id_text.isdigit():
        return None

    return Player.objects.filter(pk=int(player_id_text)).first()


def _resolve_character(actor_ref: str | None) -> Player | Mob | None:
    player = _resolve_player(actor_ref)
    if player is not None:
        return player
    if not actor_ref:
        return None
    actor_ref = str(actor_ref)
    if not actor_ref.startswith("mob."):
        return None
    mob_id_text = actor_ref.split(".", 1)[1]
    if not mob_id_text.isdigit():
        return None
    return Mob.objects.filter(pk=int(mob_id_text)).first()


def _target_mob_id_from_event(event_data: dict) -> int | None:
    target = event_data.get("target")
    if not isinstance(target, dict):
        return None
    target_ref = target.get("key")
    if target_ref:
        target_ref = str(target_ref)
        if not target_ref.startswith("mob."):
            return None
        target_id = target_ref.split(".", 1)[1]
    else:
        if str(target.get("type") or "mob").lower() != "mob":
            return None
        target_id = target.get("id")
    try:
        return int(target_id)
    except (TypeError, ValueError):
        return None


def _on_cmd_say_success(
    event_data: dict,
    actor_key: str | None,
    connection_id: str | None,
) -> None:
    player = _resolve_player(_extract_actor_key(event_data, actor_key))
    if not player or not player.room_id:
        return

    message_text = event_data.get("text")
    execute_mob_event_triggers(
        event=adv_consts.MOB_REACTION_EVENT_SAYING,
        actor=player,
        room=player.room_id,
        match_text=str(message_text or ""),
        connection_id=connection_id,
        source_event_data=event_data,
    )


def _on_cmd_move_success(
    event_data: dict,
    actor_key: str | None,
    connection_id: str | None,
) -> None:
    player = _resolve_player(_extract_actor_key(event_data, actor_key))
    if not player:
        return

    room_id = None
    room_data = event_data.get("room")
    if isinstance(room_data, dict):
        room_id = room_data.get("id")
    if not room_id:
        room_id = player.room_id
    if not room_id:
        return

    direction = event_data.get("direction")
    origin_room_id = None
    origin_room_data = event_data.get("origin_room")
    if isinstance(origin_room_data, dict):
        origin_room_id = origin_room_data.get("id")

    if origin_room_id:
        execute_room_event_triggers(
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_EXIT,
            actor=player,
            room=origin_room_id,
            origin_room_id=origin_room_id,
            destination_room_id=room_id,
            direction=direction,
            connection_id=connection_id,
        )

    execute_mob_event_triggers(
        event=adv_consts.MOB_REACTION_EVENT_ENTERING,
        actor=player,
        room=room_id,
        connection_id=connection_id,
    )

    execute_room_event_triggers(
        event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
        actor=player,
        room=room_id,
        origin_room_id=origin_room_id,
        destination_room_id=room_id,
        direction=direction,
        connection_id=connection_id,
    )


def _on_transfer_enter(
    event_data: dict,
    actor_key: str | None,
    connection_id: str | None,
) -> None:
    gate_claims: list[tuple[str, str]] = []
    with _release_trigger_gates_on_error(gate_claims), transaction.atomic():
        if event_data.get(FINAL_TRANSFER_ENTER_KEY) is False:
            return
        actor = _resolve_character(_extract_actor_key(event_data, actor_key))
        if not actor:
            return
        try:
            event_runtime_world_id = int(
                event_data.get(
                    TRANSFER_RUNTIME_WORLD_KEY,
                    event_data.get("runtime_world_id"),
                )
            )
        except (TypeError, ValueError):
            event_runtime_world_id = None
        if (
            event_runtime_world_id is not None
            and actor.world_id != event_runtime_world_id
        ):
            return
        if isinstance(actor, Player):
            if not actor.in_game:
                return
            try:
                event_location_sequence = int(
                    event_data.get(
                        TRANSFER_LOCATION_SEQUENCE_KEY,
                        event_data.get("location_sequence"),
                    )
                )
            except (TypeError, ValueError):
                event_location_sequence = None
            if (
                event_location_sequence is not None
                and int(actor.location_sequence or 0)
                != event_location_sequence
            ):
                return
        else:
            event_location_sequence = None
            if actor.is_pending_deletion:
                return

        room_id = None
        destination = event_data.get("destination_room")
        if isinstance(destination, dict):
            room_id = destination.get("id")
        if not room_id:
            room_id = getattr(actor, "room_id", None)
        try:
            room_id = int(room_id)
        except (TypeError, ValueError):
            return

        if isinstance(actor, Player):
            # Match typed-step lock order: runtime-room advisory lock, then
            # Player. This prevents a concurrent movement from slipping
            # between arrival validation and its reactions/aggro without
            # introducing the Player -> advisory inversion that would
            # deadlock against a simultaneous Trigger start.
            from spawns.trigger_steps import lock_trigger_runtime_room

            lock_trigger_runtime_room(
                runtime_world_id=actor.world_id,
                room_id=room_id,
            )
            actor = (
                Player.objects.select_for_update()
                .filter(pk=actor.id)
                .first()
            )
            if actor is None:
                return
            if (
                event_runtime_world_id is not None
                and actor.world_id != event_runtime_world_id
            ):
                return
            if (
                not actor.in_game
                or actor.room_id != room_id
                or (
                    event_location_sequence is not None
                    and int(actor.location_sequence or 0)
                    != event_location_sequence
                )
            ):
                return

        # Outbox delivery can be delayed, and one committed step may contain
        # more than one transfer. React only to the actor's current arrival;
        # historical intermediate destinations must not act on them remotely
        # or scan their final room for aggression more than once.
        if actor.room_id != room_id:
            return

        with capture_game_events() as reaction_events:
            execute_mob_event_triggers(
                event=adv_consts.MOB_REACTION_EVENT_ENTERING,
                actor=actor,
                room=room_id,
                connection_id=connection_id,
                isolate_runtime_world=True,
                source_event_data=event_data,
                capture_output=True,
                gate_claim_collector=gate_claims,
            )

        derived_events = list(reaction_events)
        if isinstance(actor, Player):
            # Transfer commands publish their lifecycle before destination
            # aggression. Queue the derived combat output transactionally so
            # Trigger-step transfers never scan or publish while the original
            # step still holds its resource locks.
            from spawns.actions.combat import ScanRoomAggroAction

            actor.refresh_from_db(
                fields=["room", "location_sequence", "in_game", "world"],
            )
            if (
                actor.in_game
                and actor.room_id == room_id
                and (
                    event_location_sequence is None
                    or int(actor.location_sequence or 0)
                    == event_location_sequence
                )
            ):
                aggro_result = ScanRoomAggroAction().execute(actor.id)
                derived_events.extend(aggro_result.events)

        if derived_events:
            enqueue_game_events(derived_events)
            transaction.on_commit(
                flush_game_event_outbox,
                robust=True,
            )


def _on_affect_death(
    event_data: dict,
    actor_key: str | None,
    connection_id: str | None,
) -> None:
    player = _resolve_player(_extract_actor_key(event_data, actor_key))
    if not player:
        return

    room_id = None
    room_data = event_data.get("room")
    if isinstance(room_data, dict):
        room_id = room_data.get("id")
    if not room_id:
        room_id = player.room_id
    if not room_id:
        return

    origin_room_id = None
    origin_room_data = event_data.get("origin_room")
    if isinstance(origin_room_data, dict):
        origin_room_id = origin_room_data.get("id")

    execute_room_event_triggers(
        event=adv_consts.TRIGGER_EVENT_AFTER_DEATH_ROOM_ENTER,
        actor=player,
        room=room_id,
        origin_room_id=origin_room_id,
        destination_room_id=room_id,
        connection_id=connection_id,
    )


def _on_affect_social(
    event_data: dict,
    actor_key: str | None,
    connection_id: str | None,
) -> None:
    # As in WR1, only a player-originated social aimed directly at a mob can
    # drive that mob's social reaction. Bystander mobs never scan this event.
    player = _resolve_player(_extract_actor_key(event_data, actor_key))
    target_mob_id = _target_mob_id_from_event(event_data)
    if not player or not player.room_id or target_mob_id is None:
        return

    execute_mob_event_triggers(
        event=adv_consts.MOB_REACTION_EVENT_SOCIAL,
        actor=player,
        room=player.room_id,
        match_text=str(event_data.get("social") or ""),
        connection_id=connection_id,
        isolate_runtime_world=True,
        target_mob_id=target_mob_id,
        source_event_data=event_data,
    )


_EVENT_SUBSCRIPTIONS: dict[str, TriggerSubscriptionHandler] = {
    "affect.death": _on_affect_death,
    "affect.social": _on_affect_social,
    "cmd.say.success": _on_cmd_say_success,
    "cmd.move.success": _on_cmd_move_success,
    "notification./transfer.enter": _on_transfer_enter,
}


def dispatch_trigger_subscriptions_for_event(
    *,
    event_type: str,
    event_data: dict | None,
    actor_key: str | None = None,
    connection_id: str | None = None,
) -> None:
    handler = _EVENT_SUBSCRIPTIONS.get(str(event_type or "").strip().lower())
    if not handler:
        return

    data = event_data if isinstance(event_data, dict) else {}
    try:
        event_id = uuid.UUID(str(data.get("_event_id") or ""))
    except (TypeError, ValueError, AttributeError):
        event_id = None
    if event_id is None:
        handler(data, actor_key, connection_id)
        return

    with transaction.atomic():
        _, created = EventSubscriptionReceipt.objects.get_or_create(
            event_id=event_id,
            subscriber="triggers",
        )
        if not created:
            return
        handler(data, actor_key, connection_id)
