from __future__ import annotations

from typing import Callable

from config import constants as adv_consts
from spawns.models import Player
from spawns.triggers import execute_mob_event_triggers


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
        option_text=str(message_text or ""),
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

    execute_mob_event_triggers(
        event=adv_consts.MOB_REACTION_EVENT_ENTERING,
        actor=player,
        room=room_id,
        connection_id=connection_id,
    )


_EVENT_SUBSCRIPTIONS: dict[str, TriggerSubscriptionHandler] = {
    "cmd.say.success": _on_cmd_say_success,
    "cmd.move.success": _on_cmd_move_success,
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
    handler(data, actor_key, connection_id)
