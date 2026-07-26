from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from config import constants as adv_consts
from core.computations import compute_stats
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    get_state_snapshot,
    increment_state_values,
)
from core.world_config import inherited_system_config
from spawns.actions.base import ActionError
from spawns.actions.effects import clear_actor_effect_cache
from spawns.events import GameEvent, enqueue_game_events
from spawns.models import (
    ActiveEffect,
    CombatEncounter,
    CombatParticipant,
    DuelMatch,
    DuelParticipant,
    Player,
)
from worlds.models import InstanceRun, Room, World


DUELS_FOUGHT_STATE_KEY = "duels_fought"
DUELS_WON_STATE_KEY = "duels_won"
DUELS_LOST_STATE_KEY = "duels_lost"
DUEL_CHALLENGE_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class DuelActionResult:
    events: list[GameEvent] = field(default_factory=list)
    match_id: int | None = None
    state_sync_player_ids: tuple[int, ...] = ()


def _expire_pending_duels(*, player_ids: list[int] | None = None) -> None:
    queryset = DuelMatch.objects.filter(
        status=DuelMatch.STATUS_PENDING,
        expires_at__lte=timezone.now(),
    )
    if player_ids:
        queryset = queryset.filter(
            Q(challenger_id__in=player_ids)
            | Q(challenged_id__in=player_ids)
        )
    queryset.update(status=DuelMatch.STATUS_EXPIRED)


def _locked_players(player_ids: list[int]) -> dict[int, Player]:
    players = list(
        Player.objects.select_for_update(of=("self",))
        .select_related(
            "world",
            "world__context",
            "room",
            "room__transfer_to",
            "room__transfer_to__world",
            "room__transfer_to__world__config",
        )
        .filter(pk__in=sorted(set(player_ids)))
        .order_by("id")
    )
    return {player.id: player for player in players}


def _duel_lobby(player: Player) -> tuple[World, Room]:
    if not player.world_id or not player.room_id:
        raise ActionError(
            "You must be at a dueling arena entrance.",
            code="duel_entrance_required",
        )
    base_world = player.world.context
    if base_world is None or base_world.instance_of_id:
        raise ActionError(
            "You can only start a duel from the base world.",
            code="duel_base_world_required",
        )
    entry_room = player.room.transfer_to
    template_world = entry_room.world if entry_room else None
    if (
        template_world is None
        or template_world.instance_of_id != base_world.id
        or not template_world.config_id
        or template_world.config.pvp_mode != adv_consts.PVP_MODE_MATCH
    ):
        raise ActionError(
            "There is no dueling arena entrance here.",
            code="duel_entrance_required",
        )
    return template_world, entry_room


def _open_match_for_players(player_ids: list[int]):
    return (
        DuelMatch.objects.select_for_update()
        .filter(status__in=DuelMatch.OPEN_STATUSES)
        .filter(
            Q(challenger_id__in=player_ids)
            | Q(challenged_id__in=player_ids)
        )
        .order_by("id")
        .first()
    )


def _validate_duel_entry_state(player_ids: list[int]) -> None:
    if CombatEncounter.objects.filter(
        player_id__in=player_ids,
        status=CombatEncounter.STATUS_ACTIVE,
    ).exists():
        raise ActionError(
            "Both players must leave combat before the duel can begin.",
            code="duel_combat_active",
        )
    if ActiveEffect.objects.filter(
        scope=ActiveEffect.SCOPE_CHARACTER,
        is_hostile=True,
        remaining_rounds__gt=0,
    ).filter(
        Q(target_player_id__in=player_ids)
        | Q(source_player_id__in=player_ids)
    ).exists():
        raise ActionError(
            "Both players must wait for hostile effects to end before the duel can begin.",
            code="duel_hostile_effect_active",
        )


def challenge_duel(challenger_id: int, challenged_id: int) -> DuelActionResult:
    if challenger_id == challenged_id:
        raise ActionError("You cannot duel yourself.", code="duel_self")

    with transaction.atomic():
        players = _locked_players([challenger_id, challenged_id])
        challenger = players.get(challenger_id)
        challenged = players.get(challenged_id)
        if challenger is None or challenged is None:
            raise ActionError("You do not see them here.", code="target_missing")
        if (
            not challenger.in_game
            or not challenged.in_game
            or challenger.world_id != challenged.world_id
            or challenger.room_id != challenged.room_id
        ):
            raise ActionError("You do not see them here.", code="target_missing")

        template_world, entry_room = _duel_lobby(challenger)
        target_template, target_entry = _duel_lobby(challenged)
        if (
            target_template.id != template_world.id
            or target_entry.id != entry_room.id
        ):
            raise ActionError(
                "Both players must be at the same dueling arena entrance.",
                code="duel_entrance_mismatch",
            )

        _expire_pending_duels(player_ids=[challenger_id, challenged_id])
        existing = _open_match_for_players([challenger_id, challenged_id])
        if existing:
            raise ActionError(
                "One of you already has an open duel.",
                code="duel_already_open",
            )

        match = DuelMatch.objects.create(
            base_world=template_world.instance_of,
            template_world=template_world,
            entrance_room=challenger.room,
            challenger=challenger,
            challenged=challenged,
            expires_at=timezone.now() + DUEL_CHALLENGE_TTL,
        )
        DuelParticipant.objects.bulk_create([
            DuelParticipant(
                match=match,
                player=challenger,
                role=DuelParticipant.ROLE_CONTESTANT,
                team=1,
            ),
            DuelParticipant(
                match=match,
                player=challenged,
                role=DuelParticipant.ROLE_CONTESTANT,
                team=2,
            ),
        ])

    return DuelActionResult(
        match_id=match.id,
        events=[
            GameEvent(
                type="cmd.duel.challenge.success",
                recipients=[challenger.key],
                data={
                    "match_id": match.id,
                    "challenged_id": challenged.id,
                    "challenged_name": challenged.name,
                    "expires_at": match.expires_at.isoformat(),
                },
                text=(
                    f"You challenge {challenged.name} to a duel. "
                    "The challenge expires in five minutes."
                ),
            ),
            GameEvent(
                type="notification.duel.challenged",
                recipients=[challenged.key],
                data={
                    "match_id": match.id,
                    "challenger_id": challenger.id,
                    "challenger_name": challenger.name,
                    "expires_at": match.expires_at.isoformat(),
                },
                text=(
                    f"{challenger.name} challenges you to a duel. "
                    "Use `duel accept` or `duel decline`."
                ),
            ),
        ],
    )


def _pending_received_match(
    player_id: int,
    challenger_id: int | None = None,
    *,
    match_id: int | None = None,
    lock: bool = True,
):
    queryset = DuelMatch.objects.filter(
        challenged_id=player_id,
        status=DuelMatch.STATUS_PENDING,
    )
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    if challenger_id is not None:
        queryset = queryset.filter(challenger_id=challenger_id)
    if match_id is not None:
        queryset = queryset.filter(pk=match_id)
    return queryset.select_related(
        "base_world",
        "template_world",
        "template_world__config",
        "entrance_room",
        "challenger",
        "challenged",
    ).order_by("-created_ts").first()


def accept_duel(
    challenged_id: int,
    *,
    challenger_id: int | None = None,
) -> DuelActionResult:
    from worlds.instances import (
        create_fresh_instance_run,
        enter_players_into_run,
    )

    with transaction.atomic():
        _expire_pending_duels(player_ids=[challenged_id])
        candidate = _pending_received_match(
            challenged_id,
            challenger_id,
            lock=False,
        )
        if candidate is None:
            raise ActionError(
                "You do not have that pending duel challenge.",
                code="duel_challenge_missing",
            )
        if not candidate.challenger_id or not candidate.challenged_id:
            raise ActionError(
                "That duel challenge is no longer valid.",
                code="duel_challenge_invalid",
            )

        players = _locked_players([
            candidate.challenger_id,
            candidate.challenged_id,
        ])
        match = _pending_received_match(
            challenged_id,
            challenger_id,
            match_id=candidate.id,
        )
        if match is None:
            raise ActionError(
                "That duel challenge is no longer pending.",
                code="duel_challenge_missing",
            )
        if match.expires_at <= timezone.now():
            match.status = DuelMatch.STATUS_EXPIRED
            match.save(update_fields=["status"])
            raise ActionError(
                "That duel challenge has expired.",
                code="duel_challenge_expired",
            )

        challenger = players.get(match.challenger_id)
        challenged = players.get(match.challenged_id)
        if challenger is None or challenged is None:
            raise ActionError(
                "That duel challenge is no longer valid.",
                code="duel_challenge_invalid",
            )
        if (
            not challenger.in_game
            or not challenged.in_game
            or challenger.world_id != challenged.world_id
            or challenger.room_id != match.entrance_room_id
            or challenged.room_id != match.entrance_room_id
        ):
            raise ActionError(
                "Both players must remain online at the arena entrance.",
                code="duel_players_moved",
            )
        template_world, entry_room = _duel_lobby(challenger)
        challenged_template, challenged_entry = _duel_lobby(challenged)
        if (
            template_world.id != match.template_world_id
            or challenged_template.id != match.template_world_id
            or entry_room.id != challenged_entry.id
        ):
            raise ActionError(
                "The arena entrance is no longer configured for this duel.",
                code="duel_entrance_changed",
            )

        _validate_duel_entry_state([challenger.id, challenged.id])

        run = create_fresh_instance_run(
            match.template_world,
            leader=challenger,
            member_ids=[challenged.id],
        )
        now = timezone.now()
        match.run = run
        match.status = DuelMatch.STATUS_ACTIVE
        match.started_at = now
        match.save(update_fields=["run", "status", "started_at"])
        match.participants.update(joined_at=now, exited_at=None)

        enter_players_into_run(
            run,
            players_and_transfer_rooms=(
                (challenger, match.entrance_room),
                (challenged, match.entrance_room),
            ),
            entry_room=entry_room,
        )

    text = (
        f"The duel between {challenger.name} and {challenged.name} begins. "
        "Defeat your opponent to win; `flee` only breaks the current engagement."
    )
    return DuelActionResult(
        match_id=match.id,
        state_sync_player_ids=(challenger.id, challenged.id),
        events=[
            GameEvent(
                type="notification.duel.started",
                recipients=[challenger.key, challenged.key],
                data={
                    "match_id": match.id,
                    "instance_ref": run.ref,
                    "challenger_id": challenger.id,
                    "challenged_id": challenged.id,
                },
                text=text,
            )
        ],
    )


def decline_duel(
    challenged_id: int,
    *,
    challenger_id: int | None = None,
) -> DuelActionResult:
    with transaction.atomic():
        _expire_pending_duels(player_ids=[challenged_id])
        match = _pending_received_match(challenged_id, challenger_id)
        if match is None:
            raise ActionError(
                "You do not have that pending duel challenge.",
                code="duel_challenge_missing",
            )
        match.status = DuelMatch.STATUS_DECLINED
        match.save(update_fields=["status"])
        challenger = match.challenger
        challenged = match.challenged

    recipients = [
        player.key
        for player in (challenger, challenged)
        if player is not None
    ]
    challenged_name = challenged.name if challenged else "The challenged player"
    return DuelActionResult(
        match_id=match.id,
        events=[
            GameEvent(
                type="notification.duel.declined",
                recipients=recipients,
                data={"match_id": match.id},
                text=f"{challenged_name} declines the duel challenge.",
            )
        ],
    )


def cancel_duel(challenger_id: int) -> DuelActionResult:
    with transaction.atomic():
        _expire_pending_duels(player_ids=[challenger_id])
        match = (
            DuelMatch.objects.select_for_update(of=("self",))
            .select_related("challenger", "challenged")
            .filter(
                challenger_id=challenger_id,
                status=DuelMatch.STATUS_PENDING,
            )
            .order_by("-created_ts")
            .first()
        )
        if match is None:
            raise ActionError(
                "You do not have a pending challenge to cancel.",
                code="duel_challenge_missing",
            )
        match.status = DuelMatch.STATUS_CANCELLED
        match.save(update_fields=["status"])

    recipients = [
        player.key
        for player in (match.challenger, match.challenged)
        if player is not None
    ]
    return DuelActionResult(
        match_id=match.id,
        events=[
            GameEvent(
                type="notification.duel.cancelled",
                recipients=recipients,
                data={"match_id": match.id},
                text="The duel challenge is cancelled.",
            )
        ],
    )


def get_active_duel_match(
    player: Player,
    *,
    lock: bool = False,
) -> DuelMatch | None:
    if not player.world_id:
        return None
    queryset = DuelMatch.objects
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return (
        queryset.select_related("run", "run__spawned_world")
        .filter(
            run__spawned_world_id=player.world_id,
            status=DuelMatch.STATUS_ACTIVE,
            participants__player_id=player.id,
            participants__role=DuelParticipant.ROLE_CONTESTANT,
        )
        .order_by("id")
        .first()
    )


def duel_match_for_runtime_player(player: Player) -> DuelMatch | None:
    if not player.world_id:
        return None
    return (
        DuelMatch.objects.select_related("run")
        .filter(
            run__spawned_world_id=player.world_id,
            participants__player_id=player.id,
            participants__role=DuelParticipant.ROLE_CONTESTANT,
        )
        .order_by("-created_ts")
        .first()
    )


def duel_combat_block_reason(player: Player) -> str | None:
    template_world = getattr(player.world, "context", None)
    if template_world is None or not template_world.instance_of_id:
        return None

    match = duel_match_for_runtime_player(player)
    if match and match.status == DuelMatch.STATUS_ACTIVE:
        return "You may only fight the opposing contestant in this duel."
    if match and match.status == DuelMatch.STATUS_COMPLETED:
        return "This duel is over. Leave the arena and start a new duel to fight again."

    config = getattr(template_world, "config", None)
    if not config or config.pvp_mode != adv_consts.PVP_MODE_MATCH:
        return None
    return "Combat is only available to contestants in an active duel."


def validate_duel_attack(
    attacker: Player,
    target: Player,
    *,
    lock: bool = False,
) -> DuelMatch:
    if attacker.id == target.id:
        raise ActionError("You cannot attack yourself.", code="duel_self")
    if (
        attacker.world_id != target.world_id
        or attacker.room_id is None
        or attacker.room_id != target.room_id
        or not target.in_game
    ):
        raise ActionError("You do not see them here.", code="target_missing")
    rules_config = inherited_system_config(attacker.world)
    if rules_config and not rules_config.allow_combat:
        raise ActionError("Combat is disabled here.", code="combat_disabled")

    match = get_active_duel_match(attacker, lock=lock)
    if match is None:
        message = duel_combat_block_reason(attacker)
        raise ActionError(
            message or "You are not in an active duel.",
            code="duel_complete" if "over" in str(message or "") else "duel_inactive",
        )
    participants = {
        participant.player_id: participant
        for participant in match.participants.filter(
            role=DuelParticipant.ROLE_CONTESTANT,
            player_id__in=[attacker.id, target.id],
        )
    }
    attacker_participant = participants.get(attacker.id)
    target_participant = participants.get(target.id)
    if (
        attacker_participant is None
        or target_participant is None
        or attacker_participant.team == target_participant.team
    ):
        raise ActionError(
            "You may only attack an opposing contestant in your duel.",
            code="duel_target_forbidden",
        )
    if int(target.health or 0) <= 0:
        raise ActionError(
            "That contestant has already been defeated.",
            code="duel_target_defeated",
        )
    return match


def duel_opponent(
    player: Player,
    *,
    match: DuelMatch | None = None,
    lock: bool = False,
) -> Player | None:
    match = match or get_active_duel_match(player, lock=lock)
    if match is None:
        return None
    queryset = Player.objects
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return (
        queryset.filter(
            duel_participations__match=match,
            duel_participations__role=DuelParticipant.ROLE_CONTESTANT,
        )
        .exclude(pk=player.id)
        .order_by("id")
        .first()
    )


def _restore_duelist_resources(players: list[Player]) -> None:
    for player in players:
        stats = compute_stats(
            player.level,
            player.archetype,
            char=player,
            world=player.world,
        )
        player.health_max = max(1, int(stats.get("health_max") or 1))
        player.energy_max = max(0, int(stats.get("energy_max") or 0))
        player.stamina_max = max(0, int(stats.get("stamina_max") or 0))
        player.health = player.health_max
        player.energy = player.energy_max
        player.stamina = player.stamina_max
        player.save(update_fields=[
            "health",
            "energy",
            "stamina",
        ])


def _duel_announcement_recipients(base_world_id: int) -> list[str]:
    player_ids = (
        Player.objects.filter(in_game=True)
        .filter(
            Q(world__context_id=base_world_id)
            | Q(world__context__instance_of_id=base_world_id)
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    return [f"player.{player_id}" for player_id in player_ids]


def abandon_duel_run(run: InstanceRun | int) -> bool:
    """Close an idle duel run without awarding a win, loss, or fight."""
    run_id = run.id if isinstance(run, InstanceRun) else int(run)
    with transaction.atomic():
        locked_run = (
            InstanceRun.objects.select_for_update()
            .filter(pk=run_id)
            .first()
        )
        if locked_run is None:
            return False
        match = (
            DuelMatch.objects.select_for_update(of=("self",))
            .filter(run=locked_run, status=DuelMatch.STATUS_ACTIVE)
            .first()
        )
        if match is None:
            return False

        participant_ids = list(
            match.participants.filter(
                role=DuelParticipant.ROLE_CONTESTANT,
            ).values_list("player_id", flat=True)
        )
        players = list(
            Player.objects.select_for_update(of=("self",))
            .select_related("world", "world__context")
            .filter(pk__in=sorted(participant_ids))
            .order_by("id")
        )
        now = timezone.now()
        outcome = {
            "resolution": "abandoned",
            "completed_at": now.isoformat(),
        }
        match.status = DuelMatch.STATUS_CANCELLED
        match.completed_at = now
        match.outcome = outcome
        match.save(update_fields=[
            "status",
            "completed_at",
            "outcome",
        ])

        encounter_ids = list(
            CombatEncounter.objects.filter(duel_match=match).values_list(
                "id",
                flat=True,
            )
        )
        if encounter_ids:
            CombatParticipant.objects.filter(
                encounter_id__in=encounter_ids,
            ).update(
                is_active=False,
                pending_ability={},
                pending_flee={},
            )
            ActiveEffect.objects.filter(
                encounter_id__in=encounter_ids,
                scope=ActiveEffect.SCOPE_ENCOUNTER,
            ).delete()
            CombatEncounter.objects.filter(
                id__in=encounter_ids,
                status=CombatEncounter.STATUS_ACTIVE,
            ).update(
                status=CombatEncounter.STATUS_FINISHED,
                next_resolution_ts=None,
            )
        ActiveEffect.objects.filter(
            world=locked_run.spawned_world,
            target_player_id__in=participant_ids,
            is_hostile=True,
        ).delete()
        _restore_duelist_resources(players)
        for player in players:
            clear_actor_effect_cache(player)

        locked_run.status = InstanceRun.STATUS_ABANDONED
        locked_run.completed_at = now
        locked_run.last_active_at = now
        locked_run.outcome = outcome
        locked_run.save(update_fields=[
            "status",
            "completed_at",
            "last_active_at",
            "outcome",
        ])
    return True


def resolve_duel_defeat(
    match: DuelMatch | int,
    winner: Player | int,
    loser: Player | int,
    *,
    reason: str = "defeat",
    leading_events: list[GameEvent] | None = None,
) -> list[GameEvent]:
    """
    Claim and finalize a duel exactly once.

    Result events, plus any lethal combat events supplied by the caller, are
    placed in one ordered transactional-outbox batch. Returning an empty list
    keeps callers from double-publishing that batch.
    """
    match_id = match.id if isinstance(match, DuelMatch) else int(match)
    winner_id = winner.id if isinstance(winner, Player) else int(winner)
    loser_id = loser.id if isinstance(loser, Player) else int(loser)

    match_ref = DuelMatch.objects.only("run_id").filter(pk=match_id).first()
    if match_ref is None:
        return []

    with transaction.atomic():
        run = None
        if match_ref.run_id:
            run = (
                InstanceRun.objects.select_for_update()
                .filter(pk=match_ref.run_id)
                .first()
            )
        locked_match = (
            DuelMatch.objects.select_for_update(of=("self",))
            .select_related("base_world", "base_world__config", "run")
            .filter(pk=match_id)
            .first()
        )
        if locked_match is None or locked_match.status != DuelMatch.STATUS_ACTIVE:
            return []

        participant_ids = list(
            locked_match.participants.filter(
                role=DuelParticipant.ROLE_CONTESTANT,
            ).values_list("player_id", flat=True)
        )
        if (
            winner_id == loser_id
            or winner_id not in participant_ids
            or loser_id not in participant_ids
        ):
            raise ActionError(
                "The duel result does not match its contestants.",
                code="duel_result_invalid",
            )
        players_by_id = _locked_players(participant_ids)
        winner_player = players_by_id.get(winner_id)
        loser_player = players_by_id.get(loser_id)
        if winner_player is None or loser_player is None:
            raise ActionError(
                "A duel contestant no longer exists.",
                code="duel_result_invalid",
            )

        now = timezone.now()
        outcome = {
            "resolution": reason,
            "winner_id": winner_player.id,
            "winner_name": winner_player.name,
            "loser_id": loser_player.id,
            "loser_name": loser_player.name,
            "completed_at": now.isoformat(),
        }
        locked_match.status = DuelMatch.STATUS_COMPLETED
        locked_match.winner = winner_player
        locked_match.loser = loser_player
        locked_match.completed_at = now
        locked_match.outcome = outcome
        locked_match.save(update_fields=[
            "status",
            "winner",
            "loser",
            "completed_at",
            "outcome",
        ])
        locked_match.participants.filter(player=winner_player).update(
            result=DuelParticipant.RESULT_WON,
        )
        locked_match.participants.filter(player=loser_player).update(
            result=DuelParticipant.RESULT_LOST,
        )

        winner_record = increment_state_values(
            STATE_SCOPE_CHARACTER,
            winner_player,
            {
                DUELS_FOUGHT_STATE_KEY: 1,
                DUELS_WON_STATE_KEY: 1,
                DUELS_LOST_STATE_KEY: 0,
            },
        )
        loser_record = increment_state_values(
            STATE_SCOPE_CHARACTER,
            loser_player,
            {
                DUELS_FOUGHT_STATE_KEY: 1,
                DUELS_WON_STATE_KEY: 0,
                DUELS_LOST_STATE_KEY: 1,
            },
        )
        winner_fought = winner_record[DUELS_FOUGHT_STATE_KEY]
        winner_won = winner_record[DUELS_WON_STATE_KEY]
        winner_lost = winner_record[DUELS_LOST_STATE_KEY]
        loser_fought = loser_record[DUELS_FOUGHT_STATE_KEY]
        loser_won = loser_record[DUELS_WON_STATE_KEY]
        loser_lost = loser_record[DUELS_LOST_STATE_KEY]

        encounter_ids = list(
            CombatEncounter.objects.filter(duel_match=locked_match)
            .values_list("id", flat=True)
        )
        if encounter_ids:
            CombatParticipant.objects.filter(
                encounter_id__in=encounter_ids,
            ).update(
                is_active=False,
                pending_ability={},
                pending_flee={},
            )
            ActiveEffect.objects.filter(
                encounter_id__in=encounter_ids,
                scope=ActiveEffect.SCOPE_ENCOUNTER,
            ).delete()
            CombatEncounter.objects.filter(
                id__in=encounter_ids,
                status=CombatEncounter.STATUS_ACTIVE,
            ).update(
                status=CombatEncounter.STATUS_FINISHED,
                next_resolution_ts=None,
            )

        if run is not None:
            ActiveEffect.objects.filter(
                world=run.spawned_world,
                is_hostile=True,
            ).filter(
                Q(target_player=winner_player)
                | Q(target_player=loser_player)
            ).delete()
            run.status = InstanceRun.STATUS_COMPLETED
            run.completed_at = now
            run.outcome = outcome
            run.last_active_at = now
            run.save(update_fields=[
                "status",
                "completed_at",
                "outcome",
                "last_active_at",
            ])

        _restore_duelist_resources([winner_player, loser_player])

        result_text = (
            f"{winner_player.name} has defeated {loser_player.name} in a duel."
        )
        result_data = {
            "match_id": locked_match.id,
            **outcome,
            "state_keys": {
                "fought": DUELS_FOUGHT_STATE_KEY,
                "won": DUELS_WON_STATE_KEY,
                "lost": DUELS_LOST_STATE_KEY,
            },
            "records": {
                str(winner_player.id): {
                    "fought": winner_fought,
                    "won": winner_won,
                    "lost": winner_lost,
                },
                str(loser_player.id): {
                    "fought": loser_fought,
                    "won": loser_won,
                    "lost": loser_lost,
                },
            },
        }
        from spawns.state_payloads import (
            serialize_actor,
            serialize_char_from_player,
        )

        completion_text = (
            f"{result_text} Combat is now disabled in this arena. "
            "Leave and start a new duel to fight again."
        )
        events = []
        for player, opponent in (
            (winner_player, loser_player),
            (loser_player, winner_player),
        ):
            events.append(
                GameEvent(
                    type="notification.duel.completed",
                    recipients=[player.key],
                    data={
                        **result_data,
                        "actor": serialize_actor(
                            player,
                            player.room,
                        ).model_dump(),
                        "target": serialize_char_from_player(
                            opponent,
                            viewer=player,
                        ).model_dump(),
                    },
                    text=completion_text,
                )
            )
        if locked_match.base_world.config.announce_duel_results:
            announcement_recipients = _duel_announcement_recipients(
                locked_match.base_world_id,
            )
            if announcement_recipients:
                events.append(
                    GameEvent(
                        type="notification.duel.announcement",
                        recipients=announcement_recipients,
                        data={
                            "match_id": locked_match.id,
                            **outcome,
                        },
                        text=result_text,
                    )
                )
        enqueue_game_events([*(leading_events or []), *events])

    return []


def surrender_duel(player_id: int) -> DuelActionResult:
    player = (
        Player.objects.select_related("world", "world__context")
        .filter(pk=player_id)
        .first()
    )
    if player is None:
        raise ActionError("Player not found.", code="player_missing")
    match = get_active_duel_match(player)
    if match is None:
        raise ActionError("You are not in an active duel.", code="duel_inactive")
    opponent = duel_opponent(player, match=match)
    if opponent is None:
        raise ActionError(
            "Your opponent is no longer available.",
            code="duel_opponent_missing",
        )
    resolve_duel_defeat(match, opponent, player, reason="surrender")
    final_match = DuelMatch.objects.only(
        "status",
        "winner_id",
        "loser_id",
    ).get(pk=match.id)
    if final_match.loser_id != player.id:
        if final_match.winner_id == player.id:
            raise ActionError(
                "The duel is already over. You won.",
                code="duel_complete",
            )
        raise ActionError(
            "The duel is already over.",
            code="duel_complete",
        )
    return DuelActionResult(match_id=match.id)


def _duel_record_text(player: Player) -> str:
    state = get_state_snapshot(STATE_SCOPE_CHARACTER, player)

    def _count(key: str) -> int:
        try:
            return max(0, int(state.get(key, 0) or 0))
        except (TypeError, ValueError):
            return 0

    fought = _count(DUELS_FOUGHT_STATE_KEY)
    won = _count(DUELS_WON_STATE_KEY)
    lost = _count(DUELS_LOST_STATE_KEY)
    return f"Record: {fought} fought, {won} won, {lost} lost."


def duel_status_text(player: Player) -> str:
    _expire_pending_duels(player_ids=[player.id])
    record = _duel_record_text(player)
    active = get_active_duel_match(player)
    if active:
        opponent = duel_opponent(player, match=active)
        opponent_name = opponent.name if opponent else "an unavailable opponent"
        return (
            f"You are dueling {opponent_name}. "
            "`flee` breaks the current engagement but does not forfeit. "
            f"Use `duel surrender` to concede. {record}"
        )
    runtime_match = duel_match_for_runtime_player(player)
    if runtime_match and runtime_match.status == DuelMatch.STATUS_COMPLETED:
        if runtime_match.winner_id == player.id:
            result = "You won this duel."
        elif runtime_match.loser_id == player.id:
            result = "You lost this duel."
        else:
            result = "This duel is over."
        return (
            f"{result} Combat is disabled in this arena; use `leave` before "
            f"starting another duel. {record}"
        )
    pending_received = (
        DuelMatch.objects.select_related("challenger")
        .filter(
            challenged=player,
            status=DuelMatch.STATUS_PENDING,
        )
        .order_by("-created_ts")
        .first()
    )
    if pending_received:
        challenger_name = (
            pending_received.challenger.name
            if pending_received.challenger
            else "Someone"
        )
        return (
            f"{challenger_name} has challenged you. "
            f"Use `duel accept` or `duel decline`. {record}"
        )
    pending_sent = (
        DuelMatch.objects.select_related("challenged")
        .filter(
            challenger=player,
            status=DuelMatch.STATUS_PENDING,
        )
        .order_by("-created_ts")
        .first()
    )
    if pending_sent:
        challenged_name = (
            pending_sent.challenged.name
            if pending_sent.challenged
            else "that player"
        )
        return (
            f"Your challenge to {challenged_name} is pending. "
            f"Use `duel cancel` to withdraw it. {record}"
        )
    return (
        "Use `duel <player>` with both players at a dueling arena entrance "
        f"to issue a challenge. {record}"
    )
