from __future__ import annotations

from collections.abc import Iterable

from django.db.models import F

from spawns.events import GameEvent
from spawns.models import CombatEncounter, CombatParticipant, Player


def active_prepared_ability_slugs(player: Player) -> list[str]:
    if not player.room_id:
        return []

    pending_abilities = (
        CombatEncounter.objects.filter(
            player=player,
            world_id=player.world_id,
            room_id=player.room_id,
            status=CombatEncounter.STATUS_ACTIVE,
            mob_id__isnull=False,
            mob__is_pending_deletion=False,
            mob__health__gt=0,
            mob__world_id=player.world_id,
            mob__room_id=player.room_id,
        )
        .exclude(pending_player_ability={})
        .order_by("id")
        .values_list("pending_player_ability", flat=True)
    )
    pvp_pending_abilities = (
        CombatParticipant.objects.filter(
            player=player,
            is_active=True,
            encounter__world_id=player.world_id,
            encounter__room_id=player.room_id,
            encounter__status=CombatEncounter.STATUS_ACTIVE,
            encounter__duel_match_id__isnull=False,
        )
        .exclude(pending_ability={})
        .order_by("encounter_id", "id")
        .values_list("pending_ability", flat=True)
    )
    slugs: list[str] = []
    seen_slugs: set[str] = set()
    for pending in [*pending_abilities, *pvp_pending_abilities]:
        if not isinstance(pending, dict):
            continue
        slug = str(pending.get("ability") or "").strip().lower()
        if slug and slug not in seen_slugs:
            seen_slugs.add(slug)
            slugs.append(slug)
    return slugs


def ability_prepare_state_event(player: Player) -> GameEvent:
    return GameEvent(
        type="player.ability_preparations.update",
        recipients=[player.key],
        data={"abilities": active_prepared_ability_slugs(player)},
    )


def ability_prepare_state_events_for_players(
    player_ids: Iterable[int],
) -> list[GameEvent]:
    normalized_ids = sorted({int(player_id) for player_id in player_ids if player_id})
    if not normalized_ids:
        return []

    slugs_by_player_id = {player_id: [] for player_id in normalized_ids}
    seen_slugs_by_player_id = {player_id: set() for player_id in normalized_ids}
    pending_abilities = (
        CombatEncounter.objects.filter(
            player_id__in=normalized_ids,
            world_id=F("player__world_id"),
            room_id=F("player__room_id"),
            status=CombatEncounter.STATUS_ACTIVE,
            mob_id__isnull=False,
            mob__is_pending_deletion=False,
            mob__health__gt=0,
            mob__world_id=F("player__world_id"),
            mob__room_id=F("player__room_id"),
        )
        .exclude(pending_player_ability={})
        .order_by("id")
        .values_list("player_id", "pending_player_ability")
    )
    for player_id, pending in pending_abilities:
        if not isinstance(pending, dict):
            continue
        slug = str(pending.get("ability") or "").strip().lower()
        player_slugs = slugs_by_player_id[player_id]
        seen_slugs = seen_slugs_by_player_id[player_id]
        if slug and slug not in seen_slugs:
            seen_slugs.add(slug)
            player_slugs.append(slug)

    pvp_pending_abilities = (
        CombatParticipant.objects.filter(
            player_id__in=normalized_ids,
            is_active=True,
            encounter__world_id=F("player__world_id"),
            encounter__room_id=F("player__room_id"),
            encounter__status=CombatEncounter.STATUS_ACTIVE,
            encounter__duel_match_id__isnull=False,
        )
        .exclude(pending_ability={})
        .order_by("encounter_id", "id")
        .values_list("player_id", "pending_ability")
    )
    for player_id, pending in pvp_pending_abilities:
        if not isinstance(pending, dict):
            continue
        slug = str(pending.get("ability") or "").strip().lower()
        player_slugs = slugs_by_player_id[player_id]
        seen_slugs = seen_slugs_by_player_id[player_id]
        if slug and slug not in seen_slugs:
            seen_slugs.add(slug)
            player_slugs.append(slug)

    return [
        GameEvent(
            type="player.ability_preparations.update",
            recipients=[f"player.{player_id}"],
            data={"abilities": slugs_by_player_id[player_id]},
        )
        for player_id in normalized_ids
    ]
