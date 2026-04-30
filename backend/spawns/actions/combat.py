from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from django.db import transaction
from django.utils import timezone

from core.combat_formulas import CombatAttackResult, resolve_attack
from core.computations import compute_stats
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.targeting import resolve_room_mob_target
from spawns.events import GameEvent
from spawns.models import CombatEncounter, Item, Mob, Player
from spawns.state_payloads import (
    door_state_lookup,
    room_payload_key_for,
    serialize_actor,
    serialize_char_from_mob,
    serialize_char_from_player,
    serialize_item,
    serialize_room,
)
from worlds.models import Room


logger = logging.getLogger(__name__)
MAX_AUTO_RESOLVE_ROUNDS = 100


@dataclass(frozen=True)
class CombatStats:
    player_health_max: int
    player_mana_max: int
    player_stamina_max: int


@dataclass(frozen=True)
class CombatStepResult:
    actor_key: str | None
    events: list[GameEvent]
    encounter_active: bool


def _player_combat_stats(player: Player) -> CombatStats:
    stats = compute_stats(
        player.level,
        player.archetype,
        char=player,
        world=player.world,
    )
    return CombatStats(
        player_health_max=max(1, int(stats.get("health_max") or 1)),
        player_mana_max=int(stats.get("mana_max") or 0),
        player_stamina_max=int(stats.get("stamina_max") or 0),
    )


def _room_payload(viewer: Player, room: Room) -> dict:
    door_states = door_state_lookup(viewer.world, [room.id])
    return serialize_room(
        room,
        {room.id: room_payload_key_for(room)},
        door_states,
        viewer=viewer,
    ).model_dump()


def _combat_recipients(player: Player, room: Room) -> list[str]:
    return [
        f"player.{pid}"
        for pid in Player.objects.filter(room_id=room.id, in_game=True)
        .exclude(pk=player.id)
        .values_list("id", flat=True)
    ]


def _ensure_corpse(mob: Mob) -> int:
    corpse = mob.inventory.filter(type="corpse").order_by("id").first()
    if corpse:
        return corpse.id

    # TODO: Consider a virtual corpse presentation if we want to stop
    # pre-creating corpse records for every spawned mob.
    return mob.create_corpse().id


def _serialize_corpse(corpse_id: int, *, viewer: Player | None = None) -> dict:
    corpse = Item.objects.select_related("template", "currency").get(pk=corpse_id)
    return serialize_item(corpse, viewer=viewer, include_inventory=True).model_dump()


def _actor_attack_text(target_name: str, result: CombatAttackResult) -> str:
    if result.outcome == "dodged":
        return f"{target_name} dodges your attack."
    if result.is_crit_hit:
        return f"You critically hit {target_name} for {result.damage_taken} damage."
    return f"You hit {target_name} for {result.damage_taken} damage."


def _actor_hit_text(actor_name: str, result: CombatAttackResult) -> str:
    if result.outcome == "dodged":
        return f"You dodge {actor_name}'s attack."
    if result.is_crit_hit:
        return f"{actor_name} critically hits you for {result.damage_taken} damage."
    return f"{actor_name} hits you for {result.damage_taken} damage."


def _room_attack_text(actor_name: str, target_name: str, result: CombatAttackResult) -> str:
    if result.outcome == "dodged":
        return f"{target_name} dodges {actor_name}'s attack."
    if result.is_crit_hit:
        return f"{actor_name} critically hits {target_name} for {result.damage_taken} damage."
    return f"{actor_name} hits {target_name} for {result.damage_taken} damage."


def _mob_death_text(mob_name: str | None) -> str:
    name = str(mob_name or "").strip() or "Something"
    return f"{name} is dead! R.I.P."


def _reward_text(
    *,
    experience_gained: int = 0,
    gold_gained: int = 0,
) -> str | None:
    lines: list[str] = []
    if experience_gained > 0:
        lines.append(f"You gain {experience_gained} experience.")
    if gold_gained > 0:
        lines.append(f"You receive {gold_gained} gold.")
    if not lines:
        return None
    return "\n".join(lines)


def _empty_corpse_payload() -> dict:
    return {"key": "", "name": "", "inventory": []}


def _combat_state_payload(char_payload: dict, *, target_payload: dict | None) -> dict:
    payload = dict(char_payload)
    payload["state"] = "combat"
    if target_payload:
        payload["target"] = {
            "id": target_payload.get("id"),
            "key": target_payload.get("key"),
            "name": target_payload.get("name"),
            "health": int(target_payload.get("health") or 0),
            "health_max": int(target_payload.get("health_max") or 0),
            "level": int(target_payload.get("level") or 1),
            "keywords": target_payload.get("keywords") or "",
        }
    else:
        payload["target"] = None
    return payload


def _engage_events(*, player: Player, room: Room, mob: Mob) -> list[GameEvent]:
    player_payload = _combat_state_payload(
        serialize_char_from_player(player).model_dump(),
        target_payload=serialize_char_from_mob(mob).model_dump(),
    )
    target_payload = _combat_state_payload(
        serialize_char_from_mob(mob).model_dump(),
        target_payload=serialize_char_from_player(player).model_dump(),
    )
    target_name = target_payload.get("name") or "them"
    return [
        GameEvent(
            type="cmd.kill.success",
            recipients=[player.key],
            data={
                "actor": player_payload,
                "target": target_payload,
                "room": _room_payload(player, room),
            },
            text=f"You engage {target_name}.",
        )
    ]


def _combat_attack_events(
    *,
    viewer: Player,
    room: Room,
    actor_payload: dict,
    target_payload: dict,
    result: CombatAttackResult,
    round_id: str,
    actor_text: str,
    room_text: str,
) -> list[GameEvent]:
    data = {
        "actor": actor_payload,
        "target": target_payload,
        "attack": "attack",
        "label": "Attack",
        "round_id": round_id,
    }
    data.update(result.event_data())
    events = [
        GameEvent(
            type="notification.combat.attack",
            recipients=[viewer.key],
            data=data,
            text=actor_text,
        )
    ]
    if viewer.is_invisible:
        return events

    recipients = _combat_recipients(viewer, room)
    if recipients:
        events.append(
            GameEvent(
                type="notification.combat.attack",
                recipients=recipients,
                data=data,
                text=room_text,
            )
        )
    return events


def _finish_encounter(encounter: CombatEncounter) -> None:
    if encounter._state.adding:
        encounter.status = CombatEncounter.STATUS_FINISHED
        encounter.next_resolution_ts = None
        return

    update_fields: list[str] = []
    if encounter.status != CombatEncounter.STATUS_FINISHED:
        encounter.status = CombatEncounter.STATUS_FINISHED
        update_fields.append("status")
    if encounter.next_resolution_ts is not None:
        encounter.next_resolution_ts = None
        update_fields.append("next_resolution_ts")
    if update_fields:
        encounter.save(update_fields=update_fields)


def _schedule_encounter_resolution(encounter_id: int, delay_seconds: float) -> None:
    if delay_seconds <= 0:
        return

    def _enqueue() -> None:
        from spawns import tasks as spawn_tasks

        try:
            spawn_tasks.resolve_combat_encounter.apply_async(
                kwargs={"encounter_id": encounter_id},
                countdown=delay_seconds,
            )
        except Exception:
            logger.exception(
                "Failed to schedule combat encounter %s for delayed resolution.",
                encounter_id,
            )

    transaction.on_commit(_enqueue)


def _combat_interval(config) -> float:
    if not config:
        return 0.0
    try:
        return float(config.combat_resolution_interval or 0)
    except (TypeError, ValueError):
        return 0.0


def _apply_encounter_round(*, encounter: CombatEncounter, player: Player, target_mob: Mob, config) -> CombatStepResult:
    room = Room.objects.select_related("world", "zone").get(pk=encounter.room_id)
    stats = _player_combat_stats(player)
    player.health_max = stats.player_health_max
    player.mana_max = stats.player_mana_max
    player.stamina_max = stats.player_stamina_max

    encounter.round_number = int(encounter.round_number or 0) + 1
    encounter.last_resolution_ts = timezone.now()
    if not encounter._state.adding:
        encounter.save(update_fields=["round_number", "last_resolution_ts"])
    round_id = f"encounter:{encounter.id}:{encounter.round_number}"

    events: list[GameEvent] = []

    player_attack = resolve_attack(
        actor=player,
        target=target_mob,
        world=player.world,
    )
    if player_attack.damage_taken > 0:
        target_mob.health = max(
            0,
            int(target_mob.health or 0) - player_attack.damage_taken,
        )
        target_mob.save(update_fields=["health"])

    player_char = _combat_state_payload(
        serialize_char_from_player(player).model_dump(),
        target_payload=serialize_char_from_mob(target_mob).model_dump(),
    )
    target_char = _combat_state_payload(
        serialize_char_from_mob(target_mob).model_dump(),
        target_payload=serialize_char_from_player(player).model_dump(),
    )
    target_name = target_char.get("name") or "them"
    events.extend(
        _combat_attack_events(
            viewer=player,
            room=room,
            actor_payload=player_char,
            target_payload=target_char,
            result=player_attack,
            round_id=round_id,
            actor_text=_actor_attack_text(target_name, player_attack),
            room_text=_room_attack_text(player.name, target_name, player_attack),
        )
    )

    if target_mob.health <= 0:
        corpse_id = _ensure_corpse(target_mob)
        deceased_payload = serialize_char_from_mob(target_mob).model_dump()
        exp_reward = int(target_mob.exp_worth or 0)
        gold_reward = int(target_mob.gold or 0)
        _finish_encounter(encounter)
        target_mob.delete()

        reward_update_fields: list[str] = []
        if exp_reward:
            player.experience = int(player.experience or 0) + exp_reward
            # TODO: Trigger level-up/progression checks once WR2 leveling exists.
            reward_update_fields.append("experience")
        if gold_reward:
            # TODO: Route mob spoils through a party/share policy once WR2 grouping exists.
            player.gold = int(player.gold or 0) + gold_reward
            reward_update_fields.append("gold")
        if reward_update_fields:
            player.save(update_fields=reward_update_fields)

        actor_payload = serialize_actor(player, room).model_dump()
        corpse_payload = _serialize_corpse(corpse_id, viewer=player)
        room_payload = _room_payload(player, room)
        death_data = {
            "actor": actor_payload,
            "deceased": deceased_payload,
            "killer": serialize_char_from_player(player).model_dump(),
            "corpse": corpse_payload,
            "room": room_payload,
            "experience_gained": exp_reward,
            "gold_gained": gold_reward,
        }
        death_text = _mob_death_text(deceased_payload.get("name"))
        events.append(
            GameEvent(
                type="notification.death",
                recipients=[player.key],
                data=death_data,
                text=death_text,
            )
        )

        if not player.is_invisible:
            recipients = _combat_recipients(player, room)
            if recipients:
                events.append(
                    GameEvent(
                        type="notification.death",
                        recipients=recipients,
                        data={
                            "deceased": deceased_payload,
                            "killer": serialize_char_from_player(player).model_dump(),
                            "corpse": corpse_payload,
                        },
                        text=death_text,
                    )
                )

        reward_text = _reward_text(
            experience_gained=exp_reward,
            gold_gained=gold_reward,
        )
        if reward_text:
            events.append(
                GameEvent(
                    type="notification.reward",
                    recipients=[player.key],
                    data={
                        "actor": actor_payload,
                        "source": deceased_payload,
                        "experience_gained": exp_reward,
                        "gold_gained": gold_reward,
                    },
                    text=reward_text,
                )
            )

        events.append(
            GameEvent(
                type="quest.mob.killed",
                recipients=[],
                data={
                    "actor": actor_payload,
                    "target": deceased_payload,
                    "room": room_payload,
                    "experience_gained": exp_reward,
                    "gold_gained": gold_reward,
                },
            )
        )
        return CombatStepResult(
            actor_key=player.key,
            events=events,
            encounter_active=False,
        )

    if not target_mob.fights_back:
        return CombatStepResult(
            actor_key=player.key,
            events=events,
            encounter_active=True,
        )

    mob_attack = resolve_attack(
        actor=target_mob,
        target=player,
        world=player.world,
    )
    if mob_attack.damage_taken > 0:
        player.health = max(0, int(player.health or 0) - mob_attack.damage_taken)
        player.save(update_fields=["health"])

    mob_char = _combat_state_payload(
        serialize_char_from_mob(target_mob).model_dump(),
        target_payload=serialize_char_from_player(player).model_dump(),
    )
    player_char = _combat_state_payload(
        serialize_char_from_player(player).model_dump(),
        target_payload=serialize_char_from_mob(target_mob).model_dump(),
    )
    mob_name = mob_char.get("name") or "Something"
    events.extend(
        _combat_attack_events(
            viewer=player,
            room=room,
            actor_payload=mob_char,
            target_payload=player_char,
            result=mob_attack,
            round_id=round_id,
            actor_text=_actor_hit_text(mob_name, mob_attack),
            room_text=_room_attack_text(mob_name, player.name, mob_attack),
        )
    )

    if player.health <= 0:
        death_room = config.death_room if config and config.death_room_id else player.get_starting_room()
        player.health = stats.player_health_max
        player.mana = stats.player_mana_max
        player.stamina = stats.player_stamina_max
        player.room = death_room
        # TODO: Apply WR2 death penalties here once the penalty system exists.
        player.save(update_fields=["health", "mana", "stamina", "room"])

        _finish_encounter(encounter)

        affect_data = {
            "actor": serialize_actor(player, death_room).model_dump(),
            "room": _room_payload(player, death_room),
        }
        events.append(
            GameEvent(
                type="affect.death",
                recipients=[player.key],
                data=affect_data,
                text="You have been slain.",
            )
        )

        if not player.is_invisible:
            recipients = _combat_recipients(player, room)
            if recipients:
                events.append(
                    GameEvent(
                        type="notification.death",
                        recipients=recipients,
                        data={
                            "deceased": serialize_char_from_player(player).model_dump(),
                            "killer": serialize_char_from_mob(target_mob).model_dump(),
                            "corpse": _empty_corpse_payload(),
                        },
                        text=f"{mob_name} kills {player.name}.",
                    )
                )

        return CombatStepResult(
            actor_key=player.key,
            events=events,
            encounter_active=False,
        )

    return CombatStepResult(
        actor_key=player.key,
        events=events,
        encounter_active=True,
    )


def resolve_combat_encounter_step(
    encounter_id: int,
    *,
    auto_advance: bool,
) -> CombatStepResult:
    next_delay: float | None = None

    with transaction.atomic():
        encounter = (
            CombatEncounter.objects.select_for_update()
            .select_related("player", "world", "room")
            .filter(pk=encounter_id)
            .first()
        )
        if not encounter or encounter.status != CombatEncounter.STATUS_ACTIVE:
            return CombatStepResult(actor_key=None, events=[], encounter_active=False)

        now = timezone.now()
        if auto_advance and encounter.next_resolution_ts and encounter.next_resolution_ts > now:
            return CombatStepResult(
                actor_key=encounter.player.key,
                events=[],
                encounter_active=True,
            )

        player = Player.objects.select_for_update().get(pk=encounter.player_id)
        target_mob = (
            Mob.objects.select_for_update()
            .filter(pk=encounter.mob_id, is_pending_deletion=False)
            .first()
        )
        if not target_mob:
            _finish_encounter(encounter)
            return CombatStepResult(actor_key=player.key, events=[], encounter_active=False)

        if player.room_id != encounter.room_id or target_mob.room_id != encounter.room_id:
            _finish_encounter(encounter)
            return CombatStepResult(actor_key=player.key, events=[], encounter_active=False)

        config = player.world.effective_config
        result = _apply_encounter_round(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            config=config,
        )

        if result.encounter_active and auto_advance and encounter.resolution_interval > 0:
            encounter.next_resolution_ts = timezone.now() + timedelta(
                seconds=encounter.resolution_interval
            )
            encounter.save(update_fields=["next_resolution_ts"])
            next_delay = encounter.resolution_interval
        elif not result.encounter_active:
            _finish_encounter(encounter)

    if next_delay:
        _schedule_encounter_resolution(encounter_id, next_delay)

    return result


class KillAction:
    def _resolve_immediately(self, *, player: Player, target_mob: Mob, config) -> ActionResult:
        events: list[GameEvent] = []
        room = Room.objects.select_related("world", "zone").get(pk=player.room_id)
        stats = _player_combat_stats(player)
        player.health_max = stats.player_health_max
        player.mana_max = stats.player_mana_max
        player.stamina_max = stats.player_stamina_max

        for round_no in range(1, MAX_AUTO_RESOLVE_ROUNDS + 1):
            encounter = CombatEncounter(
                world=player.world,
                room=room,
                player=player,
                mob=target_mob,
                resolution_interval=0,
                round_number=round_no - 1,
            )
            step = _apply_encounter_round(
                encounter=encounter,
                player=player,
                target_mob=target_mob,
                config=config,
            )
            events.extend(step.events)
            if not step.encounter_active:
                return ActionResult(events=events)

        raise ActionError("Combat stalled before anyone died.", code="combat_stalled")

    def execute(self, player_id: int, target_selector: str | None) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere. Cannot kill anything.", code="no_room")

            config = player.world.effective_config
            if config and not config.allow_combat:
                raise ActionError("Combat is disabled here.", code="combat_disabled")

            room = Room.objects.select_related("world", "zone").get(pk=player.room_id)
            target_ref = resolve_room_mob_target(
                room,
                target_selector,
                empty_error="Kill what?",
                not_found_error="You don't see them here.",
            )
            target_mob = (
                Mob.objects.select_for_update()
                .filter(pk=target_ref.id, is_pending_deletion=False)
                .first()
            )
            if not target_mob:
                raise ActionError("You don't see them here.", code="target_missing")

            interval = _combat_interval(config)

            active_player_encounter = (
                CombatEncounter.objects.select_for_update()
                .filter(
                    player=player,
                    status=CombatEncounter.STATUS_ACTIVE,
                )
                .first()
            )
            if active_player_encounter:
                active_name = (
                    (
                        active_player_encounter.mob.name
                        or (
                            active_player_encounter.mob.template.name
                            if active_player_encounter.mob and active_player_encounter.mob.template
                            else "them"
                        )
                    )
                    if active_player_encounter.mob
                    else "them"
                )
                if active_player_encounter.mob_id != target_mob.id:
                    raise ActionError(
                        f"You are already fighting {active_name}.",
                        code="combat_in_progress",
                    )
                if interval != -1:
                    raise ActionError(
                        f"You are already fighting {active_name}.",
                        code="combat_in_progress",
                    )
                step = resolve_combat_encounter_step(
                    active_player_encounter.id,
                    auto_advance=False,
                )
                return ActionResult(events=step.events)

            active_mob_encounter = (
                CombatEncounter.objects.select_for_update()
                .filter(
                    mob=target_mob,
                    status=CombatEncounter.STATUS_ACTIVE,
                )
                .first()
            )
            if active_mob_encounter:
                raise ActionError(
                    f"{target_mob.name or 'They'} are already fighting someone else.",
                    code="target_busy",
                )

            if interval == 0:
                return self._resolve_immediately(
                    player=player,
                    target_mob=target_mob,
                    config=config,
                )

            encounter = CombatEncounter.objects.create(
                world=player.world,
                room=room,
                player=player,
                mob=target_mob,
                resolution_interval=interval,
                next_resolution_ts=(
                    timezone.now() + timedelta(seconds=interval)
                    if interval > 0
                    else None
                ),
            )

            events = _engage_events(player=player, room=room, mob=target_mob)

            if interval == -1:
                step = resolve_combat_encounter_step(
                    encounter.id,
                    auto_advance=False,
                )
                return ActionResult(events=[*events, *step.events])

            _schedule_encounter_resolution(encounter.id, interval)
            return ActionResult(events=events)
