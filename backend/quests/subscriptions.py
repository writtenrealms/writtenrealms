from __future__ import annotations

from dataclasses import replace
from typing import Callable
import uuid

from django.db import transaction

from spawns.models import EventSubscriptionReceipt, Player
from quests.services.discovery import refresh_player_quests
from quests.services.interactions import build_inspect_guidance_events, build_talk_guidance_events
from quests.services.progress import progress_player_quests_for_event


QuestSubscriptionHandler = Callable[[dict, str | None, str | None], None]


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


def _refresh_and_progress(
    *,
    event_type: str,
    event_data: dict,
    actor_key: str | None,
    allow_auto_start: bool = False,
):
    player = _resolve_player(_extract_actor_key(event_data, actor_key))
    if not player:
        return None, None, None

    try:
        event_id = uuid.UUID(str(event_data.get("_event_id") or ""))
    except (TypeError, ValueError, AttributeError):
        event_id = None

    def _progress(current_player):
        refresh_result = refresh_player_quests(
            current_player,
            allow_auto_start=allow_auto_start,
        )
        progress_result = progress_player_quests_for_event(
            current_player,
            event_type=event_type,
            event_data=event_data,
        )
        return current_player, refresh_result, progress_result

    if event_id is None:
        return _progress(player)

    with transaction.atomic():
        player = Player.objects.select_for_update().get(pk=player.pk)
        _, created = EventSubscriptionReceipt.objects.get_or_create(
            event_id=event_id,
            subscriber="quests",
        )
        if not created:
            return player, None, None
        return _progress(player)


def _publish_player_events(
    player: Player | None,
    events: list,
    *,
    connection_id: str | None,
) -> None:
    if not player:
        return
    if not events:
        return

    from spawns.events import publish_events

    publish_events(
        events,
        actor_key=player.key,
        connection_id=connection_id,
    )


def _on_cmd_look_success(event_data: dict, actor_key: str | None, connection_id: str | None) -> None:
    player, refresh_result, progress_result = _refresh_and_progress(
        event_type="cmd.look.success",
        event_data=event_data,
        actor_key=actor_key,
        allow_auto_start=True,
    )
    _publish_player_events(
        player,
        [*(refresh_result.events if refresh_result else []), *(progress_result.events if progress_result else [])],
        connection_id=connection_id,
    )


def _on_cmd_move_success(event_data: dict, actor_key: str | None, connection_id: str | None) -> None:
    player, refresh_result, progress_result = _refresh_and_progress(
        event_type="cmd.move.success",
        event_data=event_data,
        actor_key=actor_key,
        allow_auto_start=True,
    )
    _publish_player_events(
        player,
        [*(refresh_result.events if refresh_result else []), *(progress_result.events if progress_result else [])],
        connection_id=connection_id,
    )


def _on_cmd_say_success(event_data: dict, actor_key: str | None, connection_id: str | None) -> None:
    player, refresh_result, progress_result = _refresh_and_progress(
        event_type="cmd.say.success",
        event_data=event_data,
        actor_key=actor_key,
    )
    _publish_player_events(
        player,
        [*(refresh_result.events if refresh_result else []), *(progress_result.events if progress_result else [])],
        connection_id=connection_id,
    )


def _on_cmd_talk_success(event_data: dict, actor_key: str | None, connection_id: str | None) -> None:
    player, refresh_result, progress_result = _refresh_and_progress(
        event_type="cmd.talk.success",
        event_data=event_data,
        actor_key=actor_key,
    )
    extra_events = []
    if player and progress_result and not progress_result.events:
        extra_events = build_talk_guidance_events(player, event_data)
    _publish_player_events(
        player,
        [
            *(refresh_result.events if refresh_result else []),
            *(progress_result.events if progress_result else []),
            *extra_events,
        ],
        connection_id=connection_id,
    )


def _on_cmd_inspect_success(event_data: dict, actor_key: str | None, connection_id: str | None) -> None:
    player, refresh_result, progress_result = _refresh_and_progress(
        event_type="cmd.inspect.success",
        event_data=event_data,
        actor_key=actor_key,
    )
    refresh_events = [
        event
        for event in (refresh_result.events if refresh_result else [])
        if event.type != "quest.opportunity.available"
    ]
    extra_events = []
    if player and progress_result and not progress_result.events:
        extra_events = build_inspect_guidance_events(player, event_data)
    _publish_player_events(
        player,
        [
            *refresh_events,
            *(progress_result.events if progress_result else []),
            *extra_events,
        ],
        connection_id=connection_id,
    )


def _on_quest_item_delivered(event_data: dict, actor_key: str | None, connection_id: str | None) -> None:
    player, refresh_result, progress_result = _refresh_and_progress(
        event_type="quest.item.delivered",
        event_data=event_data,
        actor_key=actor_key,
    )
    _publish_player_events(
        player,
        [*(refresh_result.events if refresh_result else []), *(progress_result.events if progress_result else [])],
        connection_id=connection_id,
    )


def _on_quest_mob_killed(event_data: dict, actor_key: str | None, connection_id: str | None) -> None:
    try:
        event_id = uuid.UUID(str(event_data.get("_event_id") or ""))
    except (TypeError, ValueError, AttributeError):
        event_id = None
    if event_id is not None:
        player = _resolve_player(_extract_actor_key(event_data, actor_key))
        if not player:
            return
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player.pk)
            _, created = EventSubscriptionReceipt.objects.get_or_create(
                event_id=event_id,
                subscriber="quests",
            )
            if not created:
                return
            refresh_result = refresh_player_quests(player, allow_auto_start=False)
            progress_result = progress_player_quests_for_event(
                player,
                event_type="quest.mob.killed",
                event_data=event_data,
            )
            derived_events = [
                *(refresh_result.events if refresh_result else []),
                *(progress_result.events if progress_result else []),
            ]
            if connection_id:
                derived_events = [
                    replace(event, connection_id=connection_id)
                    if player.key in event.recipients
                    else event
                    for event in derived_events
                ]
            if derived_events:
                from spawns.events import enqueue_game_events

                enqueue_game_events(derived_events)
        return

    player, refresh_result, progress_result = _refresh_and_progress(
        event_type="quest.mob.killed",
        event_data=event_data,
        actor_key=actor_key,
    )
    _publish_player_events(
        player,
        [*(refresh_result.events if refresh_result else []), *(progress_result.events if progress_result else [])],
        connection_id=connection_id,
    )


def _on_cmd_state_sync_success(event_data: dict, actor_key: str | None, connection_id: str | None) -> None:
    player, refresh_result, progress_result = _refresh_and_progress(
        event_type="cmd.state.sync.success",
        event_data=event_data,
        actor_key=actor_key,
        allow_auto_start=True,
    )
    _publish_player_events(
        player,
        [*(refresh_result.events if refresh_result else []), *(progress_result.events if progress_result else [])],
        connection_id=connection_id,
    )


_EVENT_SUBSCRIPTIONS: dict[str, QuestSubscriptionHandler] = {
    "affect.transfer": _on_cmd_look_success,
    "cmd.state.sync.success": _on_cmd_state_sync_success,
    "cmd.look.success": _on_cmd_look_success,
    "cmd.move.success": _on_cmd_move_success,
    "cmd.say.success": _on_cmd_say_success,
    "cmd.talk.success": _on_cmd_talk_success,
    "cmd.inspect.success": _on_cmd_inspect_success,
    "quest.item.delivered": _on_quest_item_delivered,
    "quest.mob.killed": _on_quest_mob_killed,
}


def dispatch_quest_subscriptions_for_event(
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
