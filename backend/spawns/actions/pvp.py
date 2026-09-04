from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from config import constants as adv_consts
from core.attack_routines import resolve_attack_routine
from core.world_config import inherited_system_config
from spawns.ability_prepare_state import (
    ability_prepare_state_event,
    ability_prepare_state_events_for_players,
)
from spawns.ability_intents import prioritize_ready_interrupts
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.effects import (
    ActiveEffect,
    active_combatant_effects,
    advance_character_effect_durations,
    clear_actor_effect_cache,
    next_character_effect_tick_ts,
    preventing_action_effect,
)
from spawns.actions.targeting import find_room_player_target
from spawns.events import GameEvent, persist_follow_dependent_game_events
from spawns.models import (
    CombatEncounter,
    CombatParticipant,
    DuelMatch,
    DuelParticipant,
    Player,
)
from worlds.models import InstanceRun, Room


@dataclass
class LockedPvpContext:
    run: InstanceRun
    match: DuelMatch
    encounter: CombatEncounter
    participants: list[CombatParticipant]
    players: dict[int, Player]


def _combat():
    from spawns.actions import combat

    return combat


def _duels():
    from spawns import duels

    return duels


def _cancel_pending_door_for_ability(player: Player) -> None:
    from spawns.actions.doors import (
        cancel_pending_player_door_action_durably,
    )

    cancel_pending_player_door_action_durably(
        player=player,
        code="physical_action_replaced",
        message="You stop working with the door to use an ability.",
    )


def _cancel_pending_door_for_physical_action(
    player: Player,
    *,
    message: str,
) -> list[GameEvent]:
    from spawns.actions.doors import cancel_pending_player_door_action

    return cancel_pending_player_door_action(
        player=player,
        code="physical_action_replaced",
        message=message,
    )


def _is_match_runtime(player: Player) -> bool:
    template_world = getattr(player.world, "context", None)
    config = getattr(template_world, "config", None)
    if config and config.pvp_mode == adv_consts.PVP_MODE_MATCH:
        return True
    try:
        player.world.instance_run.duel_match
    except ObjectDoesNotExist:
        return False
    return True


def active_pvp_participation(
    player: Player,
    *,
    room: Room | None = None,
    lock: bool = False,
) -> CombatParticipant | None:
    queryset = (
        CombatParticipant.objects.select_related(
            "encounter",
            "encounter__duel_match",
        )
        .filter(
            player_id=player.id,
            is_active=True,
            encounter__status=CombatEncounter.STATUS_ACTIVE,
            encounter__duel_match_id__isnull=False,
            encounter__world_id=player.world_id,
        )
        .order_by("encounter_id", "id")
    )
    if room is not None:
        queryset = queryset.filter(
            encounter__room_id=room.id,
            player__room_id=room.id,
        )
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return queryset.first()


def active_pvp_player_ids() -> set[int]:
    return set(
        CombatParticipant.objects.filter(
            player_id__isnull=False,
            is_active=True,
            encounter__status=CombatEncounter.STATUS_ACTIVE,
            encounter__duel_match_id__isnull=False,
        ).values_list("player_id", flat=True)
    )


def _locked_context(encounter_id: int) -> LockedPvpContext | None:
    refs = (
        CombatEncounter.objects.filter(
            pk=encounter_id,
            duel_match_id__isnull=False,
        )
        .values("duel_match_id", "duel_match__run_id")
        .first()
    )
    if not refs or not refs["duel_match__run_id"]:
        return None

    run = (
        InstanceRun.objects.select_for_update()
        .filter(pk=refs["duel_match__run_id"])
        .first()
    )
    if run is None:
        return None
    match = (
        DuelMatch.objects.select_for_update()
        .filter(pk=refs["duel_match_id"], run=run)
        .first()
    )
    if match is None:
        return None
    encounter = (
        CombatEncounter.objects.select_for_update()
        .select_related("world", "room", "duel_match")
        .filter(pk=encounter_id, duel_match=match)
        .first()
    )
    if encounter is None:
        return None

    participants = list(
        CombatParticipant.objects.select_for_update()
        .filter(
            encounter=encounter,
            player_id__isnull=False,
        )
        .order_by("player_id", "id")
    )
    player_ids = sorted(
        participant.player_id
        for participant in participants
        if participant.player_id
    )
    players = {
        player.id: player
        for player in Player.objects.select_for_update(of=("self",))
        .select_related("world", "room", "equipment")
        .filter(pk__in=player_ids)
        .order_by("id")
    }
    return LockedPvpContext(
        run=run,
        match=match,
        encounter=encounter,
        participants=participants,
        players=players,
    )


def _participant_map(
    context: LockedPvpContext,
) -> dict[int, CombatParticipant]:
    return {
        participant.player_id: participant
        for participant in context.participants
        if participant.player_id
    }


def _remove_invalid_duel_hostile_effects(
    context: LockedPvpContext,
) -> None:
    participants = [
        row
        for row in context.participants
        if row.is_active and row.player_id in context.players
    ]
    allowed_sources = Q(pk__isnull=True)
    for target in participants:
        for source in participants:
            if target.team != source.team:
                allowed_sources |= Q(
                    target_player_id=target.player_id,
                    source_player_id=source.player_id,
                )
    ActiveEffect.objects.filter(
        world=context.encounter.world,
        scope=ActiveEffect.SCOPE_CHARACTER,
        target_player_id__in=context.players,
        remaining_rounds__gt=0,
        is_hostile=True,
    ).exclude(allowed_sources).delete()


def _finish_locked_context(
    context: LockedPvpContext,
    *,
    preserve_flee_cost_for_player_id: int | None = None,
    refund_pending_flee: bool = True,
) -> None:
    for participant in context.participants:
        pending_flee = participant.pending_flee or {}
        if (
            refund_pending_flee
            and pending_flee
            and participant.player_id != preserve_flee_cost_for_player_id
        ):
            player = context.players.get(participant.player_id)
            if player is not None:
                player.stamina = _combat()._reconciled_flee_stamina(
                    player,
                    reserved_cost=max(
                        0,
                        int(pending_flee.get("movement_cost") or 0),
                    ),
                    replacement_cost=0,
                )
                player.save(update_fields=["stamina", "modified_ts"])
        participant.is_active = False
        participant.pending_ability = {}
        participant.pending_flee = {}
        participant.save(
            update_fields=[
                "is_active",
                "pending_ability",
                "pending_flee",
                "modified_ts",
            ]
        )
    _combat()._finish_encounter(context.encounter)


def finish_pvp_encounter(encounter_id: int) -> list[GameEvent]:
    with transaction.atomic():
        context = _locked_context(encounter_id)
        if (
            context is None
            or context.encounter.status != CombatEncounter.STATUS_ACTIVE
        ):
            return []
        player_ids = list(context.players)
        _finish_locked_context(context)
    return ability_prepare_state_events_for_players(player_ids)


def reconcile_stale_pvp_encounters(
    *,
    limit: int = 200,
) -> list[GameEvent]:
    """Finish PvP encounters whose contestants have left the encounter room.

    Ordinary movement closes a spatial duel encounter after the movement
    transaction commits. This bounded heartbeat reconciliation makes that
    cleanup durable if a worker dies between the commit and its callback.
    """
    bounded_limit = max(1, min(int(limit or 1), 1000))
    stale_encounter_ids = list(
        CombatParticipant.objects.filter(
            player_id__isnull=False,
            is_active=True,
            encounter__status=CombatEncounter.STATUS_ACTIVE,
            encounter__duel_match_id__isnull=False,
        )
        .filter(
            Q(player__room_id__isnull=True)
            | ~Q(player__room_id=F("encounter__room_id"))
            | ~Q(player__world_id=F("encounter__world_id"))
        )
        .order_by("encounter_id")
        .values_list("encounter_id", flat=True)
        .distinct()[:bounded_limit]
    )
    events: list[GameEvent] = []
    for encounter_id in stale_encounter_ids:
        events.extend(finish_pvp_encounter(encounter_id))
    return events


def _duel_match_for_attack(
    attacker: Player,
    target: Player,
    *,
    lock: bool = False,
) -> DuelMatch:
    service = _duels()
    match = service.validate_duel_attack(
        attacker,
        target,
        lock=lock,
    )
    if isinstance(match, DuelMatch):
        return match
    match = service.get_active_duel_match(attacker, lock=lock)
    if not isinstance(match, DuelMatch):
        raise ActionError(
            "You are not in an active duel.",
            code="duel_not_active",
        )
    return match


def _locked_attack_players(
    attacker_id: int,
    target_id: int,
) -> tuple[DuelMatch, Player, Player]:
    snapshots = {
        player.id: player
        for player in Player.objects.select_related("world", "room")
        .filter(pk__in=[attacker_id, target_id])
    }
    attacker = snapshots.get(attacker_id)
    target = snapshots.get(target_id)
    if attacker is None or target is None:
        raise ActionError("You don't see them here.", code="target_missing")

    match_ref = _duel_match_for_attack(attacker, target)
    if not match_ref.run_id:
        raise ActionError(
            "That duel instance is no longer available.",
            code="duel_inactive",
        )

    # Use the same lock order as encounter resolution and duel completion:
    # run -> match -> encounter -> participants -> players. Parallel command
    # workers then serialize on the small match row rather than deadlocking
    # after taking player and encounter locks in opposite orders.
    run = (
        InstanceRun.objects.select_for_update()
        .filter(
            pk=match_ref.run_id,
            status__in=InstanceRun.ACTIVE_STATUSES,
        )
        .first()
    )
    if run is None:
        raise ActionError(
            "That duel instance is no longer available.",
            code="duel_inactive",
        )
    match = (
        DuelMatch.objects.select_for_update()
        .filter(
            pk=match_ref.id,
            run=run,
            status=DuelMatch.STATUS_ACTIVE,
        )
        .first()
    )
    if match is None:
        raise ActionError(
            "That duel is no longer active.",
            code="duel_inactive",
        )
    existing_encounter = (
        CombatEncounter.objects.select_for_update()
        .filter(
            duel_match=match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        .first()
    )
    if existing_encounter is not None:
        list(
            CombatParticipant.objects.select_for_update()
            .filter(encounter=existing_encounter)
            .order_by("player_id", "id")
        )

    locked = {
        player.id: player
        for player in Player.objects.select_for_update(of=("self",))
        .select_related("world", "room", "equipment")
        .filter(pk__in=sorted([attacker_id, target_id]))
        .order_by("id")
    }
    attacker = locked.get(attacker_id)
    target = locked.get(target_id)
    if attacker is None or target is None:
        raise ActionError("You don't see them here.", code="target_missing")
    if (
        not attacker.in_game
        or not target.in_game
        or not attacker.room_id
        or attacker.world_id != target.world_id
        or attacker.room_id != target.room_id
    ):
        raise ActionError("You don't see them here.", code="target_missing")
    validated_match = _duel_match_for_attack(attacker, target)
    if validated_match.id != match.id:
        raise ActionError(
            "That duel is no longer active.",
            code="duel_inactive",
        )
    return match, attacker, target


def _locked_opener_players(
    attacker_id: int,
) -> tuple[DuelMatch, Player, Player]:
    attacker_snapshot = (
        Player.objects.select_related("world", "room")
        .filter(pk=attacker_id)
        .first()
    )
    if attacker_snapshot is None:
        raise ActionError("Player not found.", code="player_missing")
    match_ref = _duels().get_active_duel_match(attacker_snapshot)
    if match_ref is None or not match_ref.run_id:
        message = _duels().duel_combat_block_reason(attacker_snapshot)
        raise ActionError(
            message or "You are not in an active duel.",
            code=(
                "duel_complete"
                if "over" in str(message or "").lower()
                else "duel_inactive"
            ),
        )

    run = (
        InstanceRun.objects.select_for_update()
        .filter(
            pk=match_ref.run_id,
            status__in=InstanceRun.ACTIVE_STATUSES,
        )
        .first()
    )
    if run is None:
        raise ActionError(
            "That duel instance is no longer available.",
            code="duel_inactive",
        )
    match = (
        DuelMatch.objects.select_for_update()
        .filter(
            pk=match_ref.id,
            run=run,
            status=DuelMatch.STATUS_ACTIVE,
        )
        .first()
    )
    if match is None:
        raise ActionError(
            "That duel is no longer active.",
            code="duel_inactive",
        )
    existing_encounter = (
        CombatEncounter.objects.select_for_update()
        .filter(
            duel_match=match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        .first()
    )
    existing_participants: list[CombatParticipant] = []
    if existing_encounter is not None:
        existing_participants = list(
            CombatParticipant.objects.select_for_update()
            .filter(encounter=existing_encounter)
            .order_by("player_id", "id")
        )

    contestant_rows = list(
        DuelParticipant.objects.select_for_update()
        .filter(
            match=match,
            role=DuelParticipant.ROLE_CONTESTANT,
        )
        .order_by("player_id")
    )
    opponent_rows = [
        row
        for row in contestant_rows
        if row.player_id != attacker_id
    ]
    if len(contestant_rows) != 2 or len(opponent_rows) != 1:
        raise ActionError(
            "Your duel opponent is not available.",
            code="duel_opponent_missing",
        )
    opponent_id = opponent_rows[0].player_id
    locked_players = {
        player.id: player
        for player in Player.objects.select_for_update(of=("self",))
        .select_related("world", "room", "equipment")
        .filter(pk__in=sorted([attacker_id, opponent_id]))
        .order_by("id")
    }
    attacker = locked_players.get(attacker_id)
    opponent = locked_players.get(opponent_id)
    if (
        attacker is None
        or opponent is None
        or not attacker.in_game
        or not opponent.in_game
        or attacker.world_id != run.spawned_world_id
        or opponent.world_id != run.spawned_world_id
        or not attacker.room_id
        or not opponent.room_id
    ):
        raise ActionError(
            "Your duel opponent is not available.",
            code="duel_opponent_missing",
        )
    teams = {row.player_id: row.team for row in contestant_rows}
    if teams[attacker.id] == teams[opponent.id]:
        raise ActionError(
            "You cannot attack someone on your team.",
            code="duel_same_team",
        )
    if existing_encounter is not None:
        encounter_is_spatially_current = (
            existing_encounter.world_id
            == attacker.world_id
            == opponent.world_id
            and existing_encounter.room_id
            == attacker.room_id
            == opponent.room_id
            and {
                row.player_id
                for row in existing_participants
                if row.is_active
            }
            == {attacker.id, opponent.id}
        )
        if encounter_is_spatially_current:
            raise ActionError(
                "That ability can only be used out of combat.",
                code="combat_in_progress",
            )
        _finish_locked_context(
            LockedPvpContext(
                run=run,
                match=match,
                encounter=existing_encounter,
                participants=existing_participants,
                players=locked_players,
            )
        )
    rules_config = inherited_system_config(attacker.world)
    if rules_config and not rules_config.allow_combat:
        raise ActionError("Combat is disabled here.", code="combat_disabled")
    return match, attacker, opponent


def _duel_teams(
    match: DuelMatch,
    player_ids: list[int],
) -> dict[int, int]:
    teams = dict(
        DuelParticipant.objects.filter(
            match=match,
            player_id__in=player_ids,
            role=DuelParticipant.ROLE_CONTESTANT,
        ).values_list("player_id", "team")
    )
    if set(teams) != set(player_ids):
        raise ActionError(
            "Only the duel contestants can fight here.",
            code="duel_not_contestant",
        )
    if len({teams[player_id] for player_id in player_ids}) != len(player_ids):
        raise ActionError(
            "You cannot attack someone on your team.",
            code="duel_same_team",
        )
    return teams


def _initiative_order(
    encounter: CombatEncounter,
    participants: list[CombatParticipant],
    players: dict[int, Player],
) -> list[dict]:
    combat = _combat()
    refs = [
        combat._encounter_actor_ref(
            players[participant.player_id],
            side=f"team.{participant.team}",
        )
        for participant in participants
        if participant.player_id in players
    ]
    if not combat._valid_initiative_order(
        encounter.initiative_order or [],
        refs,
    ):
        encounter.initiative_order = combat._roll_initiative_order(refs)
        encounter.save(update_fields=["initiative_order", "modified_ts"])
    return list(encounter.initiative_order or [])


def _engage_events(
    *,
    attacker: Player,
    target: Player,
    room: Room,
) -> list[GameEvent]:
    combat = _combat()
    attacker_base = combat.serialize_char_from_player(attacker).model_dump()
    target_base = combat.serialize_char_from_player(target).model_dump()
    attacker_payload = combat._combat_state_payload(
        attacker_base,
        target_payload=target_base,
    )
    target_payload = combat._combat_state_payload(
        target_base,
        target_payload=attacker_base,
    )
    data = {
        "actor": attacker_payload,
        "target": target_payload,
        "room": combat._room_payload(attacker, room),
    }
    events = [
        GameEvent(
            type="cmd.kill.success",
            recipients=[attacker.key],
            data=data,
            text=f"You engage {target.name}.",
        ),
        GameEvent(
            type="notification.cmd.kill.success",
            recipients=[target.key],
            data=data,
            text=f"{attacker.name} engages you.",
        ),
    ]
    spectator_ids = (
        Player.objects.filter(
            world_id=attacker.world_id,
            room_id=room.id,
            in_game=True,
        )
        .exclude(pk__in=[attacker.id, target.id])
        .values_list("id", flat=True)
    )
    recipients = [f"player.{player_id}" for player_id in spectator_ids]
    if recipients:
        events.append(
            GameEvent(
                type="notification.cmd.kill.success",
                recipients=recipients,
                data=data,
                text=f"{attacker.name} engages {target.name}.",
            )
        )
    return events


def _create_encounter(
    *,
    match: DuelMatch,
    attacker: Player,
    target: Player,
) -> tuple[CombatEncounter, list[CombatParticipant], bool]:
    existing = (
        CombatEncounter.objects.select_for_update()
        .filter(
            duel_match=match,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        .first()
    )
    if existing is not None:
        all_participants = list(
            CombatParticipant.objects.select_for_update()
            .filter(
                encounter=existing,
            )
            .order_by("player_id")
        )
        participants = [
            participant
            for participant in all_participants
            if (
                participant.is_active
                and participant.player_id in {attacker.id, target.id}
            )
        ]
        encounter_is_spatially_current = (
            existing.world_id == attacker.world_id == target.world_id
            and existing.room_id == attacker.room_id == target.room_id
            and len(participants) == 2
            and {row.player_id for row in participants}
            == {attacker.id, target.id}
        )
        if encounter_is_spatially_current:
            return existing, participants, False

        # A committed move can outlive its on-commit cleanup callback if the
        # worker dies. Reconcile the stale encounter while holding the normal
        # run/match/encounter/player locks, then create the new room encounter
        # as part of this same command.
        _finish_locked_context(
            LockedPvpContext(
                run=match.run,
                match=match,
                encounter=existing,
                participants=all_participants,
                players={
                    attacker.id: attacker,
                    target.id: target,
                },
            )
        )

    teams = _duel_teams(match, [attacker.id, target.id])
    raw_interval = _combat()._combat_interval(
        inherited_system_config(attacker.world)
    )
    # Immediate full auto-resolution leaves no opportunity for arena movement.
    # Treat zero as explicit/manual advancement for PvP.
    interval = -1 if raw_interval == 0 else raw_interval
    try:
        with transaction.atomic():
            encounter = CombatEncounter.objects.create(
                world=attacker.world,
                room_id=attacker.room_id,
                player=attacker,
                duel_match=match,
                resolution_interval=interval,
                next_resolution_ts=(
                    timezone.now() + timedelta(seconds=interval)
                    if interval > 0
                    else None
                ),
            )
    except IntegrityError:
        encounter = (
            CombatEncounter.objects.select_for_update()
            .get(
                duel_match=match,
                status=CombatEncounter.STATUS_ACTIVE,
            )
        )
        participants = list(
            CombatParticipant.objects.select_for_update()
            .filter(
                encounter=encounter,
                player_id__in=[attacker.id, target.id],
                is_active=True,
            )
            .order_by("player_id")
        )
        return encounter, participants, False

    participants = [
        CombatParticipant.objects.create(
            encounter=encounter,
            player=actor,
            team=teams[actor.id],
        )
        for actor in sorted([attacker, target], key=lambda value: value.id)
    ]
    players = {attacker.id: attacker, target.id: target}
    _initiative_order(encounter, participants, players)
    return encounter, participants, True


def _opponent_for(
    participant: CombatParticipant,
    participants: list[CombatParticipant],
) -> CombatParticipant:
    opponents = [
        candidate
        for candidate in participants
        if (
            candidate.player_id
            and candidate.player_id != participant.player_id
            and candidate.team != participant.team
            and candidate.is_active
        )
    ]
    if len(opponents) != 1:
        raise ActionError(
            "Your duel opponent is not available.",
            code="duel_opponent_missing",
        )
    return opponents[0]


def try_execute_kill(
    player_id: int,
    target_selector: str | None,
) -> ActionResult | None:
    attacker = (
        Player.objects.select_related(
            "world",
            "world__context",
            "world__context__config",
            "world__instance_run__duel_match",
            "room",
        )
        .filter(pk=player_id)
        .first()
    )
    if attacker is None or not attacker.room_id:
        return None
    if not _is_match_runtime(attacker):
        return None
    participation = active_pvp_participation(attacker, room=attacker.room)
    target: Player | None = None
    if participation is not None:
        participants = list(
            CombatParticipant.objects.filter(
                encounter_id=participation.encounter_id,
                player_id__isnull=False,
                is_active=True,
            )
        )
        opponent = _opponent_for(participation, participants)
        target = Player.objects.filter(pk=opponent.player_id).first()
        if target_selector:
            selected = find_room_player_target(
                attacker.room,
                target_selector,
                world=attacker.world,
                exclude=attacker,
            )
            if selected is None or target is None or selected.id != target.id:
                raise ActionError(
                    "You are already fighting your duel opponent.",
                    code="combat_in_progress",
                )
    elif target_selector:
        target = find_room_player_target(
            attacker.room,
            target_selector,
            world=attacker.world,
            exclude=attacker,
        )
    if target is None:
        return None

    with transaction.atomic():
        match, attacker, target = _locked_attack_players(
            attacker.id,
            target.id,
        )
        encounter, _participants, created = _create_encounter(
            match=match,
            attacker=attacker,
            target=target,
        )
        if (
            not created
            and encounter.resolution_interval != -1
        ):
            raise ActionError(
                f"You are already fighting {target.name}.",
                code="combat_in_progress",
            )
        cancellation_events = _cancel_pending_door_for_physical_action(
            attacker,
            message="You stop working with the door to fight.",
        )
        encounter_id = encounter.id
        interval = encounter.resolution_interval
        events = [
            *cancellation_events,
            *(
                _engage_events(
                    attacker=attacker,
                    target=target,
                    room=attacker.room,
                )
                if created
                else []
            ),
        ]

    if interval == -1:
        step = resolve_pvp_encounter_step(
            encounter_id,
            auto_advance=False,
            leading_events=events,
        )
        return ActionResult(events=step.events)
    if created and interval > 0:
        _combat()._schedule_encounter_resolution(encounter_id, interval)
    return ActionResult(events=events)


def _pvp_ability_is_supported(ability) -> bool:
    for component in ability.components or []:
        selector = str(component.get("target") or "").strip().lower()
        if selector in {"room.players", "room.hostiles"}:
            return False
    return True


def _duel_scoped_self_utility_ability(ability):
    scoped_ability = copy(ability)
    scoped_ability.components = [
        (
            {**component, "target": "self"}
            if (
                isinstance(component, dict)
                and str(component.get("target") or "").strip().lower()
                == "room.allies"
            )
            else component
        )
        for component in ability.components or []
    ]
    return scoped_ability


def _execute_duel_self_utility(
    *,
    attacker_id: int,
    ability,
) -> ActionResult:
    from spawns.actions import abilities as ability_actions

    if not (ability.target or {}).get("allow_out_of_combat", True):
        raise ActionError(
            f"{ability.name} can only be used in combat.",
            code="combat_required",
        )
    with transaction.atomic():
        _match, attacker, _opponent = _locked_opener_players(attacker_id)
        _cancel_pending_door_for_ability(attacker)
        return ability_actions.AbilityAction()._resolve_self_utility(
            player=attacker,
            ability=_duel_scoped_self_utility_ability(ability),
        )


def _try_execute_room_opener_ability(
    *,
    attacker_id: int,
    ability,
    command: str,
    args: list[str],
    connection_id: str | None = None,
) -> ActionResult:
    from spawns.actions import abilities as ability_actions

    if not (ability.target or {}).get("allow_out_of_combat"):
        raise ActionError(
            f"{ability.name} can only be used out of combat.",
            code="combat_required",
        )
    with transaction.atomic():
        match, attacker, opponent = _locked_opener_players(attacker_id)
        ability_actions.validate_ability_ready(attacker, ability)
        _cancel_pending_door_for_ability(attacker)
        direction, selector = ability_actions._split_room_opener_args(
            args,
            ability=ability,
        )
        if (
            not direction
            and (ability.target or {}).get("range") == "adjacent_room"
        ):
            raise ActionError(
                f"Use a direction and target, such as {ability.slug} Rival east.",
                code="invalid_args",
            )

        move_events: list[GameEvent] = []
        move_context = None
        if direction:
            movement = ability_actions.ResolveMoveAction().execute(
                attacker,
                direction,
                source="ability",
            )
            move_context = movement.data["context"]
            for policy_event in (
                adv_consts.TRIGGER_EVENT_BEFORE_MOVE_EXIT,
                adv_consts.TRIGGER_EVENT_BEFORE_MOVE_ENTER,
            ):
                policy_result = ability_actions.evaluate_movement_policies(
                    actor=attacker,
                    event=policy_event,
                    direction=move_context.direction,
                    origin_room_id=move_context.origin_room_id,
                    destination_room_id=move_context.dest_room_id,
                    world_id=move_context.trigger_world_id,
                )
                if not policy_result.allowed:
                    raise ActionError(
                        policy_result.feedback or "You cannot go that way.",
                        code=policy_result.code,
                        data={"trigger_id": policy_result.trigger_id},
                    )
            destination_room = Room.objects.get(pk=move_context.dest_room_id)
        else:
            destination_room = attacker.room

        if opponent.room_id != destination_room.id:
            raise ActionError(
                "You don't see your duel opponent there.",
                code="target_missing",
            )
        if selector:
            selected = find_room_player_target(
                destination_room,
                selector,
                world=attacker.world,
                exclude=attacker,
            )
            if selected is None or selected.id != opponent.id:
                raise ActionError(
                    "You don't see your duel opponent there.",
                    code="target_missing",
                )

        if move_context is not None:
            ability_actions.ChangeRoomAction().execute(
                attacker,
                move_context.dest_room_id,
            )
            ability_actions.AdjustStaminaAction().execute(
                attacker,
                -move_context.movement_cost,
            )
            ability_actions.stand_player(attacker)
            attacker.save(update_fields=[
                "room",
                "location_sequence",
                "follow_move_sequence",
                "stamina",
                "state",
                "last_action_ts",
            ])
            attacker.viewed_rooms.add(move_context.dest_room_id)
            move_events = ability_actions.BuildMoveEventsAction().execute(
                move_context,
            ).events
        else:
            ability_actions.stand_player(attacker)
            attacker.save(update_fields=["state"])

        encounter, participants, created = _create_encounter(
            match=match,
            attacker=attacker,
            target=opponent,
        )
        if not created:
            raise ActionError(
                "You are already fighting your duel opponent.",
                code="combat_in_progress",
            )
        attacker_participant = next(
            row
            for row in participants
            if row.player_id == attacker.id
        )
        if (ability.target or {}).get("opener_priority"):
            encounter.opening_priority = [
                _combat().encounter_opening_priority_ref(
                    attacker,
                    side=f"team.{attacker_participant.team}",
                    source=ability.slug,
                )
            ]
            encounter.faceoff_override = True
            encounter.save(update_fields=[
                "opening_priority",
                "faceoff_override",
                "modified_ts",
            ])
        attacker_participant.pending_ability = (
            ability_actions._pending_payload(
                ability=ability,
                command=command,
                target_type="player",
                target_id=opponent.id,
                queued_round=encounter.round_number,
            )
        )
        attacker_participant.save(
            update_fields=["pending_ability", "modified_ts"],
        )
        encounter_id = encounter.id
        interval = encounter.resolution_interval
        events = [
            *move_events,
            *_engage_events(
                attacker=attacker,
                target=opponent,
                room=destination_room,
            ),
            ability_actions._ability_ack(
                player=attacker,
                ability=ability,
                replaced=False,
                target=opponent,
            ),
        ]
        events = persist_follow_dependent_game_events(
            events,
            actor_key=attacker.key,
            connection_id=connection_id,
        )

    if interval == -1:
        step = resolve_pvp_encounter_step(
            encounter_id,
            auto_advance=False,
            leading_events=events,
        )
        return ActionResult(events=step.events)
    if interval > 0:
        _combat()._schedule_encounter_resolution(encounter_id, interval)
    return ActionResult(events=events)


def try_execute_ability(
    player_id: int,
    *,
    ability,
    command: str,
    args: list[str],
    connection_id: str | None = None,
) -> ActionResult | None:
    from spawns.actions import abilities as ability_actions

    attacker = (
        Player.objects.select_related(
            "world",
            "world__context",
            "world__context__config",
            "world__instance_run__duel_match",
            "room",
        )
        .filter(pk=player_id)
        .first()
    )
    if attacker is None or not attacker.room_id:
        return None
    if not _is_match_runtime(attacker):
        return None
    if not _pvp_ability_is_supported(ability):
        raise ActionError(
            "Room-wide abilities are not supported in duels yet.",
            code="duel_ability_unsupported",
        )
    if ability_actions.ability_uses_room_opener(ability):
        return _try_execute_room_opener_ability(
            attacker_id=attacker.id,
            ability=ability,
            command=command,
            args=args,
            connection_id=connection_id,
        )
    participation = active_pvp_participation(attacker, room=attacker.room)
    target_type = str((ability.target or {}).get("type") or "hostile").lower()
    selector = " ".join(args).strip()
    target: Player | None = None

    if participation is not None:
        participant_rows = list(
            CombatParticipant.objects.filter(
                encounter_id=participation.encounter_id,
                player_id__isnull=False,
                is_active=True,
            )
        )
        opponent = _opponent_for(participation, participant_rows)
        opponent_player = Player.objects.filter(pk=opponent.player_id).first()
        if target_type in {"self", "ally"}:
            target = attacker
        else:
            target = opponent_player
            if selector:
                selected = find_room_player_target(
                    attacker.room,
                    selector,
                    world=attacker.world,
                    exclude=attacker,
                )
                if selected is None or target is None or selected.id != target.id:
                    raise ActionError(
                        "You are already fighting your duel opponent.",
                        code="combat_in_progress",
                    )
    elif target_type not in {"self", "ally"} and selector:
        target = find_room_player_target(
            attacker.room,
            selector,
            world=attacker.world,
            exclude=attacker,
        )

    if participation is None and target_type in {"self", "ally"}:
        return _execute_duel_self_utility(
            attacker_id=attacker.id,
            ability=ability,
        )

    if target is None:
        return None

    hostile_target = target if target.id != attacker.id else None
    if hostile_target is None and participation is None:
        return None

    with transaction.atomic():
        if hostile_target is not None:
            match, attacker, hostile_target = _locked_attack_players(
                attacker.id,
                hostile_target.id,
            )
            encounter, participants, created = _create_encounter(
                match=match,
                attacker=attacker,
                target=hostile_target,
            )
        else:
            context = _locked_context(participation.encounter_id)
            if (
                context is None
                or context.run.status not in InstanceRun.ACTIVE_STATUSES
                or context.match.status != DuelMatch.STATUS_ACTIVE
                or context.encounter.status != CombatEncounter.STATUS_ACTIVE
            ):
                raise ActionError(
                    "That duel encounter is no longer active.",
                    code="duel_inactive",
                )
            encounter = context.encounter
            match = context.match
            participants = context.participants
            participant_map = _participant_map(context)
            active_context_participants = [
                row
                for row in participants
                if row.is_active and row.player_id in context.players
            ]
            actors = [
                context.players[row.player_id]
                for row in active_context_participants
            ]
            participant = participant_map.get(attacker.id)
            attacker = context.players.get(attacker.id)
            if (
                len(active_context_participants) != 2
                or participant is None
                or attacker is None
                or not participant.is_active
                or any(
                    not actor.in_game
                    or actor.world_id != encounter.world_id
                    or actor.room_id != encounter.room_id
                    for actor in actors
                )
                or (
                    len(active_context_participants) == 2
                    and active_context_participants[0].team
                    == active_context_participants[1].team
                )
            ):
                raise ActionError(
                    "That duel encounter is no longer active.",
                    code="duel_inactive",
                )
            hostile_target = context.players[
                _opponent_for(
                    participant,
                    context.participants,
                ).player_id
            ]
            created = False

        ability_actions.validate_ability_ready(attacker, ability)
        _cancel_pending_door_for_ability(attacker)
        participant = next(
            (
                row
                for row in participants
                if row.player_id == attacker.id and row.is_active
            ),
            None,
        )
        if participant is None:
            raise ActionError(
                "That duel encounter is no longer active.",
                code="duel_inactive",
            )
        ability_actions._raise_if_ability_casting(participant.pending_ability)
        replaced = bool(participant.pending_ability)
        resolved_target = (
            attacker
            if target_type in {"self", "ally"}
            else hostile_target
        )
        participant.pending_ability = ability_actions._pending_payload(
            ability=ability,
            command=command,
            target_type="player",
            target_id=resolved_target.id,
            queued_round=encounter.round_number,
        )
        participant.save(update_fields=["pending_ability", "modified_ts"])
        encounter_id = encounter.id
        interval = encounter.resolution_interval
        events = (
            _engage_events(
                attacker=attacker,
                target=hostile_target,
                room=attacker.room,
            )
            if created
            else []
        )
        events.append(
            ability_actions._ability_ack(
                player=attacker,
                ability=ability,
                replaced=replaced,
                target=resolved_target,
            )
        )

    if interval == -1:
        step = resolve_pvp_encounter_step(
            encounter_id,
            auto_advance=False,
            leading_events=events,
        )
        return ActionResult(events=step.events)
    if created and interval > 0:
        _combat()._schedule_encounter_resolution(encounter_id, interval)
    return ActionResult(events=events)


def try_execute_flee(player_id: int) -> ActionResult | None:
    combat = _combat()
    player = Player.objects.select_related("world", "room").filter(pk=player_id).first()
    if player is None or not player.room_id:
        return None
    participation = active_pvp_participation(player, room=player.room)
    if participation is None:
        return None

    resolve_now = False
    events: list[GameEvent] = []
    with transaction.atomic():
        context = _locked_context(participation.encounter_id)
        if context is None:
            return None
        participant_map = _participant_map(context)
        participant = participant_map.get(player.id)
        player = context.players.get(player.id)
        if participant is None or player is None or not participant.is_active:
            return None
        if player.room_id != context.encounter.room_id:
            _finish_locked_context(context)
            return ActionResult(events=ability_prepare_state_events_for_players([player.id]))

        pending = participant.pending_flee or {}
        if pending.get("status") == "ready" and context.encounter.resolution_interval == -1:
            events.extend(
                _cancel_pending_door_for_physical_action(
                    player,
                    message="You stop working with the door to flee.",
                )
            )
            resolve_now = True
        elif pending:
            return ActionResult(
                events=[
                    *_cancel_pending_door_for_physical_action(
                        player,
                        message="You stop working with the door to flee.",
                    ),
                    GameEvent(
                        type="cmd.flee.success",
                        recipients=[player.key],
                        data={"status": pending.get("status", "preparing")},
                        text="You are already trying to flee.",
                    ),
                ]
            )
        else:
            prevention = preventing_action_effect(
                player,
                "flee",
                phase="before_action",
            )
            if prevention:
                raise ActionError(
                    combat._action_prevention_message(prevention, action="flee"),
                    code="action_prevented",
                    data=combat._action_prevention_data(prevention, action="flee"),
                )
            destination = combat._choose_flee_destination(player)
            cancellation_events = _cancel_pending_door_for_physical_action(
                player,
                message="You stop working with the door to flee.",
            )
            player.stamina = max(
                0,
                int(player.stamina or 0) - destination.movement_cost,
            )
            player.save(update_fields=["stamina", "modified_ts"])
            participant.pending_flee = {
                "status": "preparing",
                "queued_round": int(context.encounter.round_number or 0),
                "direction": destination.direction,
                "destination_room_id": destination.room_id,
                "movement_cost": destination.movement_cost,
            }
            participant.pending_ability = {}
            participant.save(
                update_fields=[
                    "pending_flee",
                    "pending_ability",
                    "modified_ts",
                ]
            )
            events = [
                *cancellation_events,
                GameEvent(
                    type="cmd.flee.success",
                    recipients=[player.key],
                    data={
                        "status": "queued",
                        "direction": destination.direction,
                        "destination_room_id": destination.room_id,
                        "movement_cost": destination.movement_cost,
                    },
                    text="You prepare to flee.",
                ),
                ability_prepare_state_event(player),
            ]
            if context.encounter.resolution_interval == -1:
                resolve_now = True

    if resolve_now:
        step = resolve_pvp_encounter_step(
            participation.encounter_id,
            auto_advance=False,
            leading_events=events or None,
        )
        return ActionResult(events=step.events)
    return ActionResult(events=events)


def _complete_ready_flee(
    *,
    context: LockedPvpContext,
    participant: CombatParticipant,
    player: Player,
    round_id: str,
) -> tuple[bool, bool, list[GameEvent]]:
    combat = _combat()
    pending = participant.pending_flee or {}
    reserved_cost = max(0, int(pending.get("movement_cost") or 0))
    direction = str(pending.get("direction") or "").strip()
    destination_room_id = int(pending.get("destination_room_id") or 0)

    prevention = preventing_action_effect(
        player,
        "flee",
        phase="before_action",
    )
    if prevention:
        participant.pending_flee = {}
        participant.pending_ability = {}
        participant.save(
            update_fields=[
                "pending_flee",
                "pending_ability",
                "modified_ts",
            ]
        )
        player.stamina = combat._reconciled_flee_stamina(
            player,
            reserved_cost=reserved_cost,
            replacement_cost=0,
        )
        player.save(update_fields=["stamina", "modified_ts"])
        return False, True, [
            combat._flee_completion_error_event(
                player,
                message=combat._action_prevention_message(
                    prevention,
                    action="flee",
                ),
                code="action_prevented",
                round_id=round_id,
                data=combat._action_prevention_data(
                    prevention,
                    action="flee",
                ),
            )
        ]

    destination = None
    route_context = combat._flee_route_context(
        player,
        movement_budget=int(player.stamina or 0) + reserved_cost,
    )
    try:
        stored = combat._flee_destination_for_direction(
            player,
            route_context,
            direction,
        )
        if stored.room_id == destination_room_id:
            destination = stored
    except ActionError:
        pass
    if destination is None:
        try:
            destination = combat._choose_flee_destination(
                player,
                route_context=route_context,
            )
        except ActionError as error:
            participant.pending_flee = {}
            participant.pending_ability = {}
            participant.save(
                update_fields=[
                    "pending_flee",
                    "pending_ability",
                    "modified_ts",
                ]
            )
            player.stamina = combat._reconciled_flee_stamina(
                player,
                reserved_cost=reserved_cost,
                replacement_cost=0,
            )
            player.save(update_fields=["stamina", "modified_ts"])
            return False, True, [
                combat._flee_completion_error_event(
                    player,
                    message=error.message,
                    code=error.code,
                    round_id=round_id,
                    data=error.data,
                )
            ]

    route_changed = (
        destination.direction != direction
        or destination.room_id != destination_room_id
        or destination.movement_cost != reserved_cost
    )
    origin_room_id = context.encounter.room_id
    door_state = combat.lock_door_state_for_movement(
        runtime_world=player.world,
        room_id=origin_room_id,
        direction=destination.direction,
    )
    if door_state and door_state.state in (
        adv_consts.DOOR_STATE_CLOSED,
        adv_consts.DOOR_STATE_LOCKED,
    ):
        participant.pending_flee = {}
        participant.pending_ability = {}
        participant.save(
            update_fields=[
                "pending_flee",
                "pending_ability",
                "modified_ts",
            ]
        )
        player.stamina = combat._reconciled_flee_stamina(
            player,
            reserved_cost=reserved_cost,
            replacement_cost=0,
        )
        player.save(update_fields=["stamina", "modified_ts"])
        return False, True, [
            combat._flee_completion_error_event(
                player,
                message="The way is blocked.",
                code="closed_door",
                round_id=round_id,
            )
        ]

    if route_changed:
        player.stamina = combat._reconciled_flee_stamina(
            player,
            reserved_cost=reserved_cost,
            replacement_cost=destination.movement_cost,
        )
    player.room_id = destination.room_id
    player.location_sequence = int(player.location_sequence or 0) + 1
    player.follow_move_sequence = int(player.follow_move_sequence or 0) + 1
    player.last_action_ts = timezone.now()
    update_fields = [
        "room",
        "location_sequence",
        "follow_move_sequence",
        "last_action_ts",
        "modified_ts",
    ]
    if route_changed:
        update_fields.append("stamina")
    player.save(update_fields=update_fields)
    player.viewed_rooms.add(destination.room_id)

    participant.pending_flee = {}
    participant.pending_ability = {}
    _finish_locked_context(
        context,
        preserve_flee_cost_for_player_id=player.id,
    )
    next_effect_tick = next_character_effect_tick_ts(player.world)
    ActiveEffect.objects.filter(
        world=player.world,
        scope=ActiveEffect.SCOPE_CHARACTER,
        target_player_id__in=context.players,
        remaining_rounds__gt=0,
    ).update(next_tick_ts=next_effect_tick)
    return True, True, [
        *combat._flee_success_events(
            player=player,
            origin_room_id=origin_room_id,
            destination_room_id=destination.room_id,
            direction=destination.direction,
            movement_cost=destination.movement_cost,
            round_id=round_id,
        ),
        *ability_prepare_state_events_for_players(context.players),
    ]


def _resolve_defeat(
    *,
    context: LockedPvpContext,
    winner: Player,
    loser: Player,
    reason: str,
    prior_events: list[GameEvent],
):
    combat = _combat()
    result_events = _duels().resolve_duel_defeat(
        context.match,
        winner,
        loser,
        reason=reason,
        leading_events=prior_events,
    )
    _finish_locked_context(context, refund_pending_flee=False)
    clear_actor_effect_cache(winner)
    clear_actor_effect_cache(loser)
    return combat.CombatStepResult(
        actor_key=winner.key,
        events=result_events,
        encounter_active=False,
    )


def _ordered_participants(
    context: LockedPvpContext,
) -> list[CombatParticipant]:
    by_player = _participant_map(context)
    order = _initiative_order(
        context.encounter,
        context.participants,
        context.players,
    )
    opening_priority = _combat()._opening_priority_for_round(
        context.encounter,
    )
    if opening_priority:
        prioritized_tokens = [
            _combat()._actor_ref_token(ref)
            for ref in opening_priority
        ]
        prioritized_set = set(prioritized_tokens)
        order = [
            ref
            for token in prioritized_tokens
            for ref in order
            if _combat()._actor_ref_token(ref) == token
        ] + [
            ref
            for ref in order
            if _combat()._actor_ref_token(ref) not in prioritized_set
        ]
    ordered = [
        by_player[int(ref.get("id") or 0)]
        for ref in order
        if (
            str(ref.get("type") or "") == "player"
            and int(ref.get("id") or 0) in by_player
        )
    ]
    for participant in context.participants:
        if participant not in ordered:
            ordered.append(participant)
    return ordered


def _primary_ordered_participants(
    ordered: list[CombatParticipant],
) -> list[CombatParticipant]:
    participants_by_key = {
        ("player", participant.player_id): participant
        for participant in ordered
    }
    actor_keys = prioritize_ready_interrupts(
        participants_by_key,
        pending_by_actor={
            actor_key: participant.pending_ability
            for actor_key, participant in participants_by_key.items()
        },
    )
    return [participants_by_key[actor_key] for actor_key in actor_keys]


def resolve_pvp_encounter_step(
    encounter_id: int,
    *,
    auto_advance: bool,
    leading_events: list[GameEvent] | None = None,
):
    combat = _combat()
    next_delay: float | None = None
    with transaction.atomic():
        context = _locked_context(encounter_id)
        if (
            context is None
            or context.encounter.status != CombatEncounter.STATUS_ACTIVE
        ):
            return combat.CombatStepResult(
                actor_key=None,
                events=[],
                encounter_active=False,
            )
        if (
            context.match.status != DuelMatch.STATUS_ACTIVE
            or context.run.status not in InstanceRun.ACTIVE_STATUSES
        ):
            player_ids = list(context.players)
            _finish_locked_context(context)
            return combat.CombatStepResult(
                actor_key=context.encounter.player.key,
                events=ability_prepare_state_events_for_players(player_ids),
                encounter_active=False,
            )

        active_participants = [
            participant
            for participant in context.participants
            if participant.is_active and participant.player_id in context.players
        ]
        if len(active_participants) != 2:
            _finish_locked_context(context)
            return combat.CombatStepResult(
                actor_key=context.encounter.player.key,
                events=[],
                encounter_active=False,
            )
        actors = [context.players[row.player_id] for row in active_participants]
        if any(
            not actor.in_game
            or actor.world_id != context.encounter.world_id
            or actor.room_id != context.encounter.room_id
            for actor in actors
        ):
            _finish_locked_context(context)
            return combat.CombatStepResult(
                actor_key=context.encounter.player.key,
                events=ability_prepare_state_events_for_players(
                    actor.id for actor in actors
                ),
                encounter_active=False,
            )
        if active_participants[0].team == active_participants[1].team:
            _finish_locked_context(context)
            return combat.CombatStepResult(
                actor_key=context.encounter.player.key,
                events=[],
                encounter_active=False,
            )
        _remove_invalid_duel_hostile_effects(context)

        now = timezone.now()
        if (
            auto_advance
            and context.encounter.next_resolution_ts
            and context.encounter.next_resolution_ts > now
        ):
            return combat.CombatStepResult(
                actor_key=context.encounter.player.key,
                events=[],
                encounter_active=True,
            )

        context.encounter.round_number = int(
            context.encounter.round_number or 0
        ) + 1
        context.encounter.last_resolution_ts = now
        context.encounter.save(
            update_fields=[
                "round_number",
                "last_resolution_ts",
                "modified_ts",
            ]
        )
        round_id = (
            f"encounter:{context.encounter.id}:"
            f"{context.encounter.round_number}"
        )
        ordered = _ordered_participants(context)
        events: list[GameEvent] = list(leading_events or [])
        skip_primary: set[int] = set()

        for participant in ordered:
            if (participant.pending_flee or {}).get("status") != "ready":
                continue
            player = context.players[participant.player_id]
            completed, consumed, flee_events = _complete_ready_flee(
                context=context,
                participant=participant,
                player=player,
                round_id=round_id,
            )
            events.extend(flee_events)
            if completed:
                return combat.CombatStepResult(
                    actor_key=player.key,
                    events=persist_follow_dependent_game_events(events),
                    encounter_active=False,
                )
            if consumed:
                skip_primary.add(player.id)
            break

        locked_player_ids = set(context.players)
        for participant in ordered:
            player = context.players[participant.player_id]
            opponent_participant = _opponent_for(
                participant,
                active_participants,
            )
            opponent = context.players[opponent_participant.player_id]
            outcome = combat._advance_character_periodic_effects(
                target_player=player,
                target_mob=None,
                encounter=context.encounter,
                viewer=player,
                round_id=round_id,
                locked_source_player_ids=locked_player_ids,
            )
            events.extend(outcome.events)
            if int(player.health or 0) <= 0:
                winner = (
                    outcome.killer
                    if isinstance(outcome.killer, Player)
                    and outcome.killer.id == opponent.id
                    else opponent
                )
                return _resolve_defeat(
                    context=context,
                    winner=winner,
                    loser=player,
                    reason="effect",
                    prior_events=events,
                )

        for participant in ordered:
            if (participant.pending_flee or {}).get("status") != "preparing":
                continue
            participant.pending_flee = {
                **participant.pending_flee,
                "status": "ready",
            }
            participant.pending_ability = {}
            participant.save(
                update_fields=[
                    "pending_flee",
                    "pending_ability",
                    "modified_ts",
                ]
            )
            player = context.players[participant.player_id]
            skip_primary.add(player.id)
            events.append(
                GameEvent(
                    type="notification.combat.flee",
                    recipients=[player.key],
                    data={"status": "preparing", "round_id": round_id},
                    text="You look for an opening to flee.",
                )
            )

        cooldown_excludes: dict[int, str | None] = {}
        for participant in _primary_ordered_participants(ordered):
            actor = context.players[participant.player_id]
            opponent_participant = _opponent_for(
                participant,
                active_participants,
            )
            target = context.players[opponent_participant.player_id]
            stats = combat._player_combat_stats(actor)
            actor.health_max = stats.player_health_max
            actor.energy_max = stats.player_energy_max
            actor.stamina_max = stats.player_stamina_max
            if actor.id in skip_primary:
                continue

            stunned = combat._consume_stun(
                context.encounter,
                target_type="player",
                target_id=actor.id,
            )
            if stunned:
                actor_payload = combat._combat_state_payload(
                    combat.serialize_char_from_player(actor).model_dump(),
                    target_payload=combat.serialize_char_from_player(
                        target
                    ).model_dump(),
                )
                events.extend(
                    combat._stun_event(
                        player=actor,
                        room=context.encounter.room,
                        target_name=actor.name,
                        target_payload=actor_payload,
                        round_id=round_id,
                    )
                )
                participant.pending_ability = {}
                participant.save(
                    update_fields=["pending_ability", "modified_ts"]
                )
                continue

            context.encounter.pending_player_ability = (
                participant.pending_ability or {}
            )
            ability_events, ability_result = (
                combat._execute_pending_player_ability(
                    encounter=context.encounter,
                    player=actor,
                    target_mob=target,
                    room=context.encounter.room,
                    round_id=round_id,
                    player_health_max=stats.player_health_max,
                    target_pending_ability=opponent_participant.pending_ability,
                )
            )
            events.extend(ability_events)
            participant.pending_ability = (
                context.encounter.pending_player_ability or {}
            )
            context.encounter.pending_player_ability = {}
            participant.save(
                update_fields=["pending_ability", "modified_ts"]
            )
            if ability_result.target_interrupted:
                opponent_participant.pending_ability = {}
                opponent_participant.save(
                    update_fields=["pending_ability", "modified_ts"]
                )
            cooldown_excludes[actor.id] = ability_result.cooldown_exclude
            if int(target.health or 0) <= 0:
                return _resolve_defeat(
                    context=context,
                    winner=actor,
                    loser=target,
                    reason="ability",
                    prior_events=events,
                )
            if ability_result.consumed_primary:
                continue

            for strike in resolve_attack_routine(
                actor=actor,
                target=target,
                world=actor.world,
            ):
                if str(getattr(strike, "target", "target")) != "target":
                    continue
                strike_outcome = combat._apply_combat_strike(
                    encounter=context.encounter,
                    player=actor,
                    target_mob=target,
                    room=context.encounter.room,
                    actor=actor,
                    target=target,
                    strike=strike,
                    round_id=round_id,
                )
                events.extend(strike_outcome.events)
                if strike_outcome.target_defeated:
                    return _resolve_defeat(
                        context=context,
                        winner=actor,
                        loser=target,
                        reason="defeat",
                        prior_events=events,
                    )

        combat._advance_non_ticking_effect_durations(context.encounter)
        effect_combatants = [
            context.players[participant.player_id]
            for participant in active_participants
        ]
        actor_state_changed: dict[int, bool] = {}
        for participant in active_participants:
            actor = context.players[participant.player_id]
            cooldown_exclude = cooldown_excludes.get(actor.id)
            cooldowns_changed = combat.decrement_ability_cooldowns(
                actor,
                exclude={cooldown_exclude} if cooldown_exclude else set(),
            )
            effects_changed = advance_character_effect_durations(
                actor,
                current_round_id=round_id,
                encounter=context.encounter,
            )
            if cooldowns_changed:
                actor.save(
                    update_fields=["ability_cooldowns", "modified_ts"]
                )
            actor_state_changed[actor.id] = bool(
                cooldown_exclude or cooldowns_changed or effects_changed
            )

        effects_by_key = active_combatant_effects(effect_combatants)
        for participant in active_participants:
            actor = context.players[participant.player_id]
            if actor_state_changed[actor.id]:
                events.append(combat._character_effect_state_event(actor))
            events.append(
                combat._combat_effect_state_event(
                    actor,
                    *(
                        combatant
                        for combatant in effect_combatants
                        if combatant.id != actor.id
                    ),
                    effects_by_key=effects_by_key,
                )
            )
            events.append(ability_prepare_state_event(actor))

        context.encounter.pending_player_ability = {}
        context.encounter.pending_flee = {}
        context.encounter.pending_mob_ability = {}
        if auto_advance and context.encounter.resolution_interval > 0:
            context.encounter.next_resolution_ts = timezone.now() + timedelta(
                seconds=context.encounter.resolution_interval
            )
            next_delay = context.encounter.resolution_interval
        context.encounter.save(
            update_fields=[
                "pending_player_ability",
                "pending_flee",
                "pending_mob_ability",
                "next_resolution_ts",
                "modified_ts",
            ]
        )
        result = combat.CombatStepResult(
            actor_key=context.encounter.player.key,
            events=events,
            encounter_active=True,
        )

    if next_delay:
        combat._schedule_encounter_resolution(encounter_id, next_delay)
    return result
