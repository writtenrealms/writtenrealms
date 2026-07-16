from __future__ import annotations

from typing import Callable
import uuid

from django.db import transaction

from config import constants as adv_consts
from spawns.models import EventSubscriptionReceipt, Mob, Player


def execute_mob_event_triggers(*args, **kwargs):
    from spawns.triggers import execute_mob_event_triggers as execute

    return execute(*args, **kwargs)


def execute_room_event_triggers(*args, **kwargs):
    from spawns.triggers import execute_room_event_triggers as execute

    return execute(*args, **kwargs)


TriggerSubscriptionHandler = Callable[[dict, str | None, str | None], None]


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
    actor = _resolve_character(_extract_actor_key(event_data, actor_key))
    if not actor:
        return

    room_id = None
    destination = event_data.get("destination_room")
    if isinstance(destination, dict):
        room_id = destination.get("id")
    if not room_id:
        room_id = getattr(actor, "room_id", None)
    if not room_id:
        return

    execute_mob_event_triggers(
        event=adv_consts.MOB_REACTION_EVENT_ENTERING,
        actor=actor,
        room=room_id,
        connection_id=connection_id,
        isolate_runtime_world=True,
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


_EVENT_SUBSCRIPTIONS: dict[str, TriggerSubscriptionHandler] = {
    "affect.death": _on_affect_death,
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
