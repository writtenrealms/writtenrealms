from __future__ import annotations

from django.db import transaction

from spawns.models import Player
from quests.services.engine import (
    QuestTransitionResult,
    active_instances_qs,
    progress_active_instance_for_event,
)


@transaction.atomic
def progress_player_quests_for_event(
    player: Player,
    *,
    event_type: str,
    event_data: dict | None = None,
) -> QuestTransitionResult:
    player = Player.objects.select_for_update().get(pk=player.pk)
    all_events = []
    last_instance = None
    for quest_instance in active_instances_qs(player):
        result = progress_active_instance_for_event(
            quest_instance,
            player=player,
            event_type=event_type,
            event_data=event_data or {},
        )
        last_instance = result.quest_instance
        all_events.extend(result.events)
    return QuestTransitionResult(
        quest_instance=last_instance,
        events=all_events,
    )
