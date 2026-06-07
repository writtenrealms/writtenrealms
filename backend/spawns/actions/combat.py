from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import random

from django.db import transaction
from django.utils import timezone

from config import constants as adv_consts
from core.combat_formulas import CombatAttackResult, resolve_attack
from core.computations import compute_stats
from core.condition_dsl import ConditionContext, evaluate_condition
from core.leveling import ExperienceGrant, apply_experience
from builders.models import AbilityDefinition
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.abilities import (
    ability_component_overrides,
    ability_cast_rounds,
    ability_is_available_to_player,
    ability_state_event,
    cooldown_remaining,
    decrement_ability_cooldowns,
    execute_state_component,
    pay_ability_cost,
    player_knows_ability,
    start_ability_cooldown,
)
from spawns.actions.movement_costs import movement_cost
from spawns.actions.player_state import stand_player
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
    safe_capitalize,
)
from worlds.models import Room


logger = logging.getLogger(__name__)
MAX_AUTO_RESOLVE_ROUNDS = 100


@dataclass(frozen=True)
class CombatStats:
    player_health_max: int
    player_energy_max: int
    player_stamina_max: int


@dataclass(frozen=True)
class CombatStepResult:
    actor_key: str | None
    events: list[GameEvent]
    encounter_active: bool


@dataclass(frozen=True)
class FleeDestination:
    direction: str
    room_id: int
    movement_cost: int


def _player_combat_stats(player: Player) -> CombatStats:
    stats = compute_stats(
        player.level,
        player.archetype,
        char=player,
        world=player.world,
    )
    return CombatStats(
        player_health_max=max(1, int(stats.get("health_max") or 1)),
        player_energy_max=int(stats.get("energy_max") or 0),
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
        return f"{safe_capitalize(target_name)} dodges your attack."
    if result.is_crit_hit:
        return f"You critically hit {target_name} for {result.damage_taken} damage."
    return f"You hit {target_name} for {result.damage_taken} damage."


def _actor_hit_text(actor_name: str, result: CombatAttackResult) -> str:
    if result.outcome == "dodged":
        return f"You dodge {actor_name}'s attack."
    if result.is_crit_hit:
        return (
            f"{safe_capitalize(actor_name)} critically hits you "
            f"for {result.damage_taken} damage."
        )
    return f"{safe_capitalize(actor_name)} hits you for {result.damage_taken} damage."


def _room_attack_text(actor_name: str, target_name: str, result: CombatAttackResult) -> str:
    if result.outcome == "dodged":
        return f"{safe_capitalize(target_name)} dodges {actor_name}'s attack."
    if result.is_crit_hit:
        return (
            f"{safe_capitalize(actor_name)} critically hits {target_name} "
            f"for {result.damage_taken} damage."
        )
    return f"{safe_capitalize(actor_name)} hits {target_name} for {result.damage_taken} damage."


def _mob_death_text(mob_name: str | None) -> str:
    name = str(mob_name or "").strip() or "Something"
    return f"{safe_capitalize(name)} is dead! R.I.P."


def _reward_text(
    *,
    experience_gained: int = 0,
    gold_gained: int = 0,
    leveling: ExperienceGrant | None = None,
) -> str | None:
    lines: list[str] = []
    if experience_gained > 0:
        lines.append(f"You gain {experience_gained} experience.")
    if leveling and leveling.leveled_up:
        lines.append(f"You are now level {leveling.new_level}!")
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
    attack: str = "attack",
    label: str = "Attack",
) -> list[GameEvent]:
    data = {
        "actor": actor_payload,
        "target": target_payload,
        "attack": attack,
        "label": label,
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


def _room_with_exits(room_id: int) -> Room:
    return Room.objects.select_related(
        "north",
        "east",
        "south",
        "west",
        "up",
        "down",
        "zone",
        "world",
    ).get(pk=room_id)


def _available_flee_destinations(player: Player) -> list[FleeDestination]:
    if not player.room_id:
        raise ActionError("You are nowhere. Cannot flee.", code="no_room")

    room = _room_with_exits(player.room_id)
    door_states = door_state_lookup(player.world, [room.id]).get(room.id, {})
    config = player.world.effective_config
    viewed_room_ids: set[int] = set()
    if config and not config.flee_to_unknown_rooms:
        viewed_room_ids = set(player.viewed_rooms.values_list("id", flat=True))

    destinations: list[FleeDestination] = []
    has_unaffordable_destination = False
    for direction in adv_consts.DIRECTIONS:
        destination = getattr(room, direction, None)
        if not destination:
            continue
        if door_states.get(direction) in ("closed", "locked"):
            continue
        if viewed_room_ids and destination.id not in viewed_room_ids:
            continue
        if destination.type == adv_consts.ROOM_TYPE_WATER:
            has_boat = player.inventory.filter(is_boat=True).exists()
            if not has_boat:
                continue
        destination_cost = movement_cost(destination)
        if int(player.stamina or 0) < destination_cost:
            has_unaffordable_destination = True
            continue
        destinations.append(
            FleeDestination(
                direction=direction,
                room_id=destination.id,
                movement_cost=destination_cost,
            )
        )

    if not destinations:
        if has_unaffordable_destination:
            raise ActionError("You are too exhausted to flee.", code="exhausted")
        raise ActionError("There is nowhere to flee to.", code="no_flee_exit")
    return destinations


def _choose_flee_destination(player: Player) -> FleeDestination:
    return random.choice(_available_flee_destinations(player))


def _flee_success_events(
    *,
    player: Player,
    origin_room_id: int,
    destination_room_id: int,
    direction: str,
    movement_cost: int,
    round_id: str | None = None,
) -> list[GameEvent]:
    destination = _room_with_exits(destination_room_id)
    actor_payload = serialize_actor(player, destination).model_dump()
    room_payload = _room_payload(player, destination)
    data = {
        "actor": actor_payload,
        "room": room_payload,
        "direction": direction,
        "movement_cost": movement_cost,
    }
    if round_id:
        data["round_id"] = round_id
    events = [
        GameEvent(
            type="cmd.flee.success",
            recipients=[player.key],
            data=data,
            text=f"You flee {direction}.",
        )
    ]

    if player.is_invisible:
        return events

    actor_char = serialize_char_from_player(player).model_dump()
    origin_recipients = (
        Player.objects.filter(room_id=origin_room_id, in_game=True)
        .exclude(pk=player.id)
        .values_list("id", flat=True)
    )
    if origin_recipients:
        events.append(
            GameEvent(
                type="notification.cmd.flee.exit",
                recipients=[f"player.{player_id}" for player_id in origin_recipients],
                data={"actor": actor_char, "direction": direction},
                text=f"{safe_capitalize(player.name)} flees {direction}.",
            )
        )

    destination_recipients = (
        Player.objects.filter(room_id=destination_room_id, in_game=True)
        .exclude(pk=player.id)
        .values_list("id", flat=True)
    )
    if destination_recipients:
        reverse_direction = adv_consts.REVERSE_DIRECTIONS[direction]
        events.append(
            GameEvent(
                type="notification.cmd.flee.enter",
                recipients=[f"player.{player_id}" for player_id in destination_recipients],
                data={"actor": actor_char, "direction": reverse_direction},
                text=(
                    f"{safe_capitalize(player.name)} arrives from the "
                    f"{reverse_direction}, looking panicked."
                ),
            )
        )

    return events


def _complete_flee(
    *,
    encounter: CombatEncounter,
    player: Player,
    round_id: str,
) -> CombatStepResult:
    pending = encounter.pending_flee or {}
    destination_room_id = int(pending.get("destination_room_id") or 0)
    direction = str(pending.get("direction") or "").strip()
    if not destination_room_id or direction not in adv_consts.DIRECTIONS:
        encounter.pending_flee = {}
        if not encounter._state.adding:
            encounter.save(update_fields=["pending_flee"])
        return CombatStepResult(
            actor_key=player.key,
            events=[
                _combat_failure_event(
                    player,
                    "You lose your chance to flee.",
                    code="flee_invalid",
                )
            ],
            encounter_active=True,
        )

    origin_room_id = encounter.room_id
    player.room_id = destination_room_id
    player.last_action_ts = timezone.now()
    player.save(update_fields=["room", "last_action_ts"])
    player.viewed_rooms.add(destination_room_id)

    encounter.pending_flee = {}
    encounter.pending_player_ability = {}
    _finish_encounter(encounter)

    return CombatStepResult(
        actor_key=player.key,
        events=_flee_success_events(
            player=player,
            origin_room_id=origin_room_id,
            destination_room_id=destination_room_id,
            direction=direction,
            movement_cost=int(pending.get("movement_cost") or 0),
            round_id=round_id,
        ),
        encounter_active=False,
    )


def _advance_flee_preparation(
    *,
    encounter: CombatEncounter,
    player: Player,
    round_id: str,
) -> list[GameEvent]:
    pending = encounter.pending_flee or {}
    if pending.get("status") != "preparing":
        return []
    encounter.pending_flee = {
        **pending,
        "status": "ready",
    }
    encounter.pending_player_ability = {}
    return [
        GameEvent(
            type="notification.combat.flee",
            recipients=[player.key],
            data={"status": "preparing", "round_id": round_id},
            text="You look for an opening to flee.",
        )
    ]


def _handle_mob_defeated(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    events: list[GameEvent],
) -> CombatStepResult:
    corpse_id = _ensure_corpse(target_mob)
    deceased_payload = serialize_char_from_mob(target_mob).model_dump()
    exp_reward = int(target_mob.exp_worth or 0)
    gold_reward = int(target_mob.gold or 0)
    _finish_encounter(encounter)
    from spawns.merchants import deactivate_merchant_runtime

    deactivate_merchant_runtime(target_mob)
    target_mob.delete()

    reward_update_fields: list[str] = []
    leveling: ExperienceGrant | None = None
    if exp_reward:
        leveling = apply_experience(player, exp_reward)
        reward_update_fields.append("experience")
        if leveling.leveled_up:
            reward_update_fields.append("level")
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
        leveling=leveling,
    )
    if reward_text:
        reward_data = {
            "actor": actor_payload,
            "source": deceased_payload,
            "experience_gained": exp_reward,
            "gold_gained": gold_reward,
        }
        if leveling:
            reward_data.update(
                {
                    "previous_level": leveling.previous_level,
                    "new_level": leveling.new_level,
                    "levels_gained": leveling.levels_gained,
                    "experience_progress": leveling.experience_progress,
                    "experience_needed": leveling.experience_needed,
                    "max_level": leveling.max_level,
                }
            )
        events.append(
            GameEvent(
                type="notification.reward",
                recipients=[player.key],
                data=reward_data,
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
                "levels_gained": leveling.levels_gained if leveling else 0,
            },
        )
    )
    return CombatStepResult(
        actor_key=player.key,
        events=events,
        encounter_active=False,
    )


@dataclass(frozen=True)
class AbilityRoundResult:
    consumed_primary: bool
    cooldown_exclude: str | None = None


def _ability_definition_for_player(player: Player, slug: str) -> AbilityDefinition | None:
    source_world = player.world.config_source_world
    return AbilityDefinition.objects.filter(
        world=source_world,
        slug=slug,
        is_active=True,
    ).first()


def _component_label(component: dict, ability: AbilityDefinition | None = None) -> str:
    text = component.get("text") or {}
    label = str(text.get("label") or "").strip()
    if label:
        return label
    if ability:
        return ability.name or ability.slug
    return "Ability"


def _combat_failure_event(player: Player, text: str, *, code: str) -> GameEvent:
    return GameEvent(
        type="notification.combat.ability_failed",
        recipients=[player.key],
        data={"error": text, "code": code},
        text=text,
    )


def _pending_cast_rounds_remaining(pending: dict, ability: AbilityDefinition) -> int:
    if "cast_rounds_remaining" in pending:
        try:
            return max(0, int(pending.get("cast_rounds_remaining") or 0))
        except (TypeError, ValueError):
            return 0
    return ability_cast_rounds(ability)


def _ability_casting_event(
    *,
    player: Player,
    ability: AbilityDefinition,
    round_id: str,
    rounds_remaining: int,
) -> GameEvent:
    if rounds_remaining > 0:
        text = f"You continue charging {ability.name}."
    else:
        text = f"You charge {ability.name}."
    return GameEvent(
        type="notification.combat.ability_casting",
        recipients=[player.key],
        data={
            "ability": {
                "slug": ability.slug,
                "name": ability.name,
                "action_type": ability.action_type,
            },
            "round_id": round_id,
            "rounds_remaining": rounds_remaining,
        },
        text=text,
    )


def _effect_applies_to(effect: dict, *, target_type: str, target_id: int) -> bool:
    target = effect.get("target") or {}
    return target.get("type") == target_type and int(target.get("id") or 0) == target_id


def _effect_ref(ref: dict | None) -> str:
    ref = ref or {}
    ref_type = str(ref.get("type") or "").strip()
    ref_id = int(ref.get("id") or 0)
    if not ref_type or not ref_id:
        return ""
    return f"{ref_type}.{ref_id}"


def _actor_ref(actor: Player | Mob) -> str:
    return f"{'player' if isinstance(actor, Player) else 'mob'}.{actor.id}"


def _append_effect(
    encounter: CombatEncounter,
    *,
    effect: str,
    source_type: str,
    source_id: int,
    target_type: str,
    target_id: int,
    duration_rounds: int,
    label: str,
    category: str = "neutral",
    primitives: list[dict] | None = None,
    tick: dict | None = None,
) -> None:
    effects = list(encounter.active_effects or [])
    effects.append(
        {
            "effect": effect,
            "category": category,
            "source": {"type": source_type, "id": source_id},
            "target": {"type": target_type, "id": target_id},
            "remaining_rounds": max(1, int(duration_rounds or 1)),
            "rounds_elapsed": 0,
            "started_round": int(encounter.round_number or 0),
            "label": label,
            "primitives": primitives or [],
            "tick": tick or {},
        }
    )
    encounter.active_effects = effects


def _consume_stun(
    encounter: CombatEncounter,
    *,
    target_type: str,
    target_id: int,
) -> bool:
    effects = list(encounter.active_effects or [])
    stunned = False
    kept: list[dict] = []
    for effect in effects:
        if effect.get("effect") == "stun" and _effect_applies_to(
            effect,
            target_type=target_type,
            target_id=target_id,
        ):
            stunned = True
            remaining = int(effect.get("remaining_rounds") or 0) - 1
            if remaining > 0:
                kept.append({**effect, "remaining_rounds": remaining})
            continue
        kept.append(effect)
    if stunned:
        encounter.active_effects = kept
    return stunned


def _stun_event(
    *,
    player: Player,
    room: Room,
    target_name: str,
    target_payload: dict,
    round_id: str,
) -> list[GameEvent]:
    data = {
        "target": target_payload,
        "effect": "stun",
        "round_id": round_id,
    }
    events = [
        GameEvent(
            type="notification.combat.effect",
            recipients=[player.key],
            data=data,
            text=f"{safe_capitalize(target_name)} is stunned and cannot act.",
        )
    ]
    if not player.is_invisible:
        recipients = _combat_recipients(player, room)
        if recipients:
            events.append(
                GameEvent(
                type="notification.combat.effect",
                recipients=recipients,
                data=data,
                text=f"{safe_capitalize(target_name)} is stunned and cannot act.",
            )
            )
    return events


def _apply_healing(
    *,
    actor: Player,
    target: Player,
    result: CombatAttackResult,
    health_max: int,
) -> None:
    if result.healing_done <= 0:
        return
    target.health = min(health_max, int(target.health or 0) + result.healing_done)


def _resource_limit(target: Player | Mob, resource: str) -> int:
    max_field = f"{resource}_max"
    default = 1 if resource == "health" else 0
    try:
        return max(default, int(getattr(target, max_field, default) or default))
    except (TypeError, ValueError):
        return default


def _resource_change_amount(
    primitive: dict,
    *,
    target: Player | Mob,
    resource: str,
) -> int:
    try:
        amount = float(primitive.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    calc = str(primitive.get("calc") or "fixed").strip().lower()
    if calc in {"percent_max", "percent_base"}:
        amount = _resource_limit(target, resource) * (amount / 100)
    return int(amount)


def _resolve_effect_target(
    target_selector: str | None,
    *,
    effect: dict,
    player: Player,
    target_mob: Mob,
) -> Player | Mob | None:
    selector = str(target_selector or "effect.target").strip().lower()
    if selector in {"actor", "self", "effect.source"}:
        return player
    if selector in {"target", "ability.target", "effect.target"}:
        target = effect.get("target") or {}
        if target.get("type") == "player" and int(target.get("id") or 0) == player.id:
            return player
        if target.get("type") == "mob" and int(target.get("id") or 0) == target_mob.id:
            return target_mob
    return None


def _execute_resource_change_primitive(
    *,
    primitive: dict,
    effect: dict,
    player: Player,
    target_mob: Mob,
    room: Room,
    round_id: str,
) -> list[GameEvent]:
    resource = str(primitive.get("resource") or "").strip().lower()
    if resource not in {"health", "energy", "stamina"}:
        return []

    target = _resolve_effect_target(
        primitive.get("target"),
        effect=effect,
        player=player,
        target_mob=target_mob,
    )
    if target is None:
        return []

    amount = _resource_change_amount(primitive, target=target, resource=resource)
    if amount == 0:
        return []

    before = int(getattr(target, resource, 0) or 0)
    limit = _resource_limit(target, resource)
    after = max(0, min(limit, before + amount))
    if after == before:
        return []

    setattr(target, resource, after)
    target.save(update_fields=[resource])

    target_payload = (
        serialize_char_from_player(target).model_dump()
        if isinstance(target, Player)
        else serialize_char_from_mob(target).model_dump()
    )
    effect_key = str(effect.get("effect") or "").strip()
    label = str(effect.get("label") or effect_key or "Effect").strip()
    delta = after - before
    data = {
        "effect": effect_key,
        "label": label,
        "target": target_payload,
        "resource": resource,
        "amount": delta,
        "current": after,
        "maximum": limit,
        "round_id": round_id,
    }
    return [
        GameEvent(
            type="notification.combat.effect",
            recipients=[player.key],
            data=data,
            text=f"{label} restores {delta} {resource}."
            if delta > 0
            else f"{label} drains {abs(delta)} {resource}.",
        )
    ]


def _execute_effect_primitives(
    *,
    primitives: list[dict],
    effect: dict,
    player: Player,
    target_mob: Mob,
    room: Room,
    round_id: str,
) -> list[GameEvent]:
    events: list[GameEvent] = []
    for primitive in primitives:
        if primitive.get("type") != "resource_change":
            continue
        events.extend(
            _execute_resource_change_primitive(
                primitive=primitive,
                effect=effect,
                player=player,
                target_mob=target_mob,
                room=room,
                round_id=round_id,
            )
        )
    return events


def _proc_condition_context(
    *,
    event_data: dict,
    player: Player,
    room: Room,
) -> ConditionContext:
    return ConditionContext(
        actor=player,
        player=player,
        room=room,
        zone=getattr(room, "zone", None),
        world=getattr(player, "world", None),
        event_data=event_data,
    )


def _execute_after_damage_procs(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    actor: Player | Mob,
    target: Player | Mob,
    result: CombatAttackResult,
    round_id: str,
    attack: str,
    label: str,
) -> list[GameEvent]:
    if result.damage_taken <= 0:
        return []

    base_event_data = {
        "actor": _actor_ref(actor),
        "target": _actor_ref(target),
        "damage_taken": result.damage_taken,
        "damage_dealt": result.damage_dealt,
        "damage_type": result.damage_type,
        "outcome": result.outcome,
        "attack": attack,
        "label": label,
        "round_id": round_id,
    }

    events: list[GameEvent] = []
    for effect in list(encounter.active_effects or []):
        for primitive in effect.get("primitives") or []:
            if primitive.get("type") != "proc" or primitive.get("phase") != "after_damage":
                continue
            event_data = {
                **base_event_data,
                "effect": {
                    "key": effect.get("effect"),
                    "source": _effect_ref(effect.get("source")),
                    "target": _effect_ref(effect.get("target")),
                },
            }
            if not evaluate_condition(
                primitive.get("conditions") or {},
                context=_proc_condition_context(
                    event_data=event_data,
                    player=player,
                    room=room,
                ),
            ):
                continue
            events.extend(
                _execute_effect_primitives(
                    primitives=primitive.get("actions") or [],
                    effect=effect,
                    player=player,
                    target_mob=target_mob,
                    room=room,
                    round_id=round_id,
                )
            )
    return events


def _execute_output_component(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    component: dict,
    ability: AbilityDefinition | None,
    round_id: str,
    player_health_max: int,
) -> tuple[list[GameEvent], bool]:
    component_type = component.get("type")
    label = _component_label(component, ability)
    events: list[GameEvent] = []

    if component_type == "healing":
        result = resolve_attack(
            actor=player,
            target=player,
            world=player.world,
            profile_key=component.get("profile"),
            overrides=ability_component_overrides(
                component,
                player=player,
                ability=ability,
                room=room,
            ) if ability else component.get("overrides") or {},
        )
        _apply_healing(
            actor=player,
            target=player,
            result=result,
            health_max=player_health_max,
        )
        actor_payload = _combat_state_payload(
            serialize_char_from_player(player).model_dump(),
            target_payload=serialize_char_from_mob(target_mob).model_dump(),
        )
        target_payload = actor_payload
        events.extend(
            _combat_attack_events(
                viewer=player,
                room=room,
                actor_payload=actor_payload,
                target_payload=target_payload,
                result=result,
                round_id=round_id,
                actor_text=f"You use {label} and heal for {result.healing_done}.",
                room_text=f"{player.name} uses {label}.",
                attack=ability.slug if ability else "effect",
                label=label,
            )
        )
        return events, result.healing_done > 0

    result = resolve_attack(
        actor=player,
        target=target_mob,
        world=player.world,
        profile_key=component.get("profile"),
        overrides=ability_component_overrides(
            component,
            player=player,
            ability=ability,
            room=room,
        ) if ability else component.get("overrides") or {},
    )
    if result.damage_taken > 0:
        target_mob.health = max(0, int(target_mob.health or 0) - result.damage_taken)
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
    if result.outcome == "dodged":
        actor_text = f"{target_name} dodges {label}."
    elif result.is_crit_hit:
        actor_text = f"You critically hit {target_name} with {label} for {result.damage_taken} damage."
    else:
        actor_text = f"You hit {target_name} with {label} for {result.damage_taken} damage."
    events.extend(
        _combat_attack_events(
            viewer=player,
            room=room,
            actor_payload=player_char,
            target_payload=target_char,
            result=result,
            round_id=round_id,
            actor_text=actor_text,
            room_text=_room_attack_text(player.name, target_name, result),
            attack=ability.slug if ability else "effect",
            label=label,
        )
    )
    events.extend(
        _execute_after_damage_procs(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            actor=player,
            target=target_mob,
            result=result,
            round_id=round_id,
            attack=ability.slug if ability else "effect",
            label=label,
        )
    )
    return events, result.outcome != "dodged" and result.damage_taken > 0


def _resolve_periodic_effects(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    round_id: str,
    player_health_max: int,
) -> list[GameEvent]:
    effects = list(encounter.active_effects or [])
    if not effects:
        return []

    events: list[GameEvent] = []
    kept: list[dict] = []
    for effect in effects:
        effect_type = effect.get("effect")
        tick = effect.get("tick") or {}
        has_tick = bool(tick)
        if not has_tick:
            kept.append(effect)
            continue

        target = effect.get("target") or {}
        if target.get("type") == "mob" and int(target.get("id") or 0) != target_mob.id:
            continue
        if target.get("type") == "player" and int(target.get("id") or 0) != player.id:
            continue

        elapsed = int(effect.get("rounds_elapsed") or 0) + 1
        remaining = int(effect.get("remaining_rounds") or 0)
        every = max(1, int(tick.get("every_rounds") or 1))
        if elapsed % every == 0:
            tick_primitives = tick.get("primitives") or []
            if tick_primitives:
                events.extend(
                    _execute_effect_primitives(
                        primitives=tick_primitives,
                        effect=effect,
                        player=player,
                        target_mob=target_mob,
                        room=room,
                        round_id=round_id,
                    )
                )
            else:
                tick_component = tick.get("component") or {}
                component_events, _ = _execute_output_component(
                    encounter=encounter,
                    player=player,
                    target_mob=target_mob,
                    room=room,
                    component=tick_component,
                    ability=None,
                    round_id=round_id,
                    player_health_max=player_health_max,
                )
                events.extend(component_events)

        remaining -= 1
        if remaining > 0:
            kept.append({**effect, "rounds_elapsed": elapsed, "remaining_rounds": remaining})

    encounter.active_effects = kept
    return events


def _effect_target_for_component(
    *,
    component: dict,
    ability: AbilityDefinition,
    pending: dict,
    player: Player,
    target_mob: Mob,
) -> tuple[str, int]:
    selector = str(component.get("target") or "ability.target").strip().lower()
    if selector in {"actor", "self", "effect.source"}:
        return "player", player.id

    pending_target = pending.get("target") or {}
    pending_target_type = str(pending_target.get("type") or "").strip().lower()
    if selector in {"target", "ability.target", "effect.target"}:
        if pending_target_type == "player":
            return "player", player.id
        if pending_target_type == "mob":
            return "mob", target_mob.id

    ability_target_type = str((ability.target or {}).get("type") or "").strip().lower()
    if ability_target_type in {"self", "ally"}:
        return "player", player.id
    return "mob", target_mob.id


def _advance_non_ticking_effect_durations(encounter: CombatEncounter) -> None:
    effects = list(encounter.active_effects or [])
    if not effects:
        return

    current_round = int(encounter.round_number or 0)
    kept: list[dict] = []
    for effect in effects:
        if effect.get("tick") or effect.get("effect") == "stun":
            kept.append(effect)
            continue
        if int(effect.get("started_round") or 0) == current_round:
            kept.append(effect)
            continue
        remaining = int(effect.get("remaining_rounds") or 0) - 1
        if remaining > 0:
            kept.append({**effect, "remaining_rounds": remaining})
    encounter.active_effects = kept


def _execute_pending_player_ability(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    round_id: str,
    player_health_max: int,
) -> tuple[list[GameEvent], AbilityRoundResult]:
    pending = encounter.pending_player_ability or {}
    if not pending:
        return [], AbilityRoundResult(consumed_primary=False)

    encounter.pending_player_ability = {}
    ability_slug = str(pending.get("ability") or "").strip().lower()
    ability = _ability_definition_for_player(player, ability_slug)
    if not ability:
        return [
            _combat_failure_event(
                player,
                "Your queued ability is no longer available.",
                code="ability_missing",
            )
        ], AbilityRoundResult(consumed_primary=False)

    if not player_knows_ability(player, ability):
        return [
            _combat_failure_event(
                player,
                f"You do not know {ability.name}.",
                code="ability_unknown",
            )
        ], AbilityRoundResult(consumed_primary=False)

    available, reason = ability_is_available_to_player(player, ability)
    if not available:
        return [
            _combat_failure_event(
                player,
                reason,
                code="ability_unavailable",
            )
        ], AbilityRoundResult(consumed_primary=False)

    remaining = cooldown_remaining(player, ability)
    if remaining > 0:
        return [
            _combat_failure_event(
                player,
                f"{ability.name} is not ready.",
                code="ability_on_cooldown",
            )
        ], AbilityRoundResult(consumed_primary=False)

    target = pending.get("target") or {}
    if target.get("type") == "mob" and int(target.get("id") or 0) != target_mob.id:
        return [
            _combat_failure_event(
                player,
                f"{ability.name} no longer has a valid target.",
                code="target_invalid",
            )
        ], AbilityRoundResult(consumed_primary=False)

    cast_rounds_remaining = _pending_cast_rounds_remaining(pending, ability)
    if cast_rounds_remaining > 0:
        next_remaining = cast_rounds_remaining - 1
        encounter.pending_player_ability = {
            **pending,
            "status": "casting",
            "cast_rounds_remaining": next_remaining,
        }
        return [
            _ability_casting_event(
                player=player,
                ability=ability,
                round_id=round_id,
                rounds_remaining=next_remaining,
            )
        ], AbilityRoundResult(consumed_primary=True)

    try:
        cost_paid = pay_ability_cost(player, ability)
    except ActionError as err:
        return [
            _combat_failure_event(player, err.message, code=err.code)
        ], AbilityRoundResult(consumed_primary=False)

    events: list[GameEvent] = []
    hit_landed = False
    health_changed = False
    for component in ability.components or []:
        component_type = component.get("type")
        if component_type in {"damage", "healing"}:
            component_events, component_hit = _execute_output_component(
                encounter=encounter,
                player=player,
                target_mob=target_mob,
                room=room,
                component=component,
                ability=ability,
                round_id=round_id,
                player_health_max=player_health_max,
            )
            events.extend(component_events)
            hit_landed = hit_landed or component_hit
            health_changed = health_changed or component_type == "healing"
            if target_mob.health <= 0:
                break
            continue

        if component_type == "state":
            state_event = execute_state_component(
                component=component,
                player=player,
                ability=ability,
                room=room,
                hit_landed=hit_landed,
                round_id=round_id,
            )
            if state_event:
                events.append(state_event)
            continue

        if component_type != "effect":
            continue
        if component.get("apply") == "on_hit" and not hit_landed:
            continue
        effect_type = component.get("effect")
        duration = int(((component.get("duration") or {}).get("rounds")) or 1)
        target_type, target_id = _effect_target_for_component(
            component=component,
            ability=ability,
            pending=pending,
            player=player,
            target_mob=target_mob,
        )
        _append_effect(
            encounter,
            effect=effect_type,
            source_type="player",
            source_id=player.id,
            target_type=target_type,
            target_id=target_id,
            duration_rounds=duration,
            label=_component_label(component, ability),
            category=component.get("category") or "neutral",
            primitives=component.get("primitives") or [],
            tick=component.get("tick") or {},
        )
        events.append(
            GameEvent(
                type="notification.combat.effect",
                recipients=[player.key],
                data={
                    "ability": ability.slug,
                    "effect": effect_type,
                    "duration_rounds": duration,
                    "round_id": round_id,
                },
                text=f"{ability.name} applies {effect_type}.",
            )
        )

    cooldown_started = start_ability_cooldown(player, ability)
    update_fields: list[str] = []
    if cost_paid:
        resource = str((ability.cost or {}).get("resource") or "").strip().lower()
        update_fields.append(resource)
    if health_changed:
        update_fields.append("health")
    if cooldown_started:
        update_fields.append("ability_cooldowns")
    if update_fields:
        player.save(update_fields=list(dict.fromkeys(field for field in update_fields if field)))

    return events, AbilityRoundResult(
        consumed_primary=True,
        cooldown_exclude=ability.slug if cooldown_started else None,
    )


def _finalize_active_round(
    *,
    encounter: CombatEncounter,
    player: Player,
    cooldown_exclude: str | None,
) -> bool:
    cooldowns_changed = decrement_ability_cooldowns(
        player,
        exclude={cooldown_exclude} if cooldown_exclude else set(),
    )
    if cooldowns_changed:
        player.save(update_fields=["ability_cooldowns"])
    if not encounter._state.adding:
        encounter.save(update_fields=["pending_player_ability", "pending_flee", "active_effects"])
    return cooldowns_changed


def _apply_encounter_round(*, encounter: CombatEncounter, player: Player, target_mob: Mob, config) -> CombatStepResult:
    room = Room.objects.select_related("world", "zone").get(pk=encounter.room_id)
    stand_player(player)
    stats = _player_combat_stats(player)
    player.health_max = stats.player_health_max
    player.energy_max = stats.player_energy_max
    player.stamina_max = stats.player_stamina_max

    encounter.round_number = int(encounter.round_number or 0) + 1
    encounter.last_resolution_ts = timezone.now()
    if not encounter._state.adding:
        encounter.save(update_fields=["round_number", "last_resolution_ts"])
    round_id = f"encounter:{encounter.id}:{encounter.round_number}"

    events: list[GameEvent] = []
    cooldown_exclude: str | None = None

    if (encounter.pending_flee or {}).get("status") == "ready":
        return _complete_flee(encounter=encounter, player=player, round_id=round_id)

    events.extend(
        _resolve_periodic_effects(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            round_id=round_id,
            player_health_max=stats.player_health_max,
        )
    )

    if target_mob.health <= 0:
        return _handle_mob_defeated(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            events=events,
        )

    flee_preparation_events = _advance_flee_preparation(
        encounter=encounter,
        player=player,
        round_id=round_id,
    )
    events.extend(flee_preparation_events)

    player_stunned = _consume_stun(
        encounter,
        target_type="player",
        target_id=player.id,
    )
    if flee_preparation_events:
        pass
    elif player_stunned:
        player_payload = _combat_state_payload(
            serialize_char_from_player(player).model_dump(),
            target_payload=serialize_char_from_mob(target_mob).model_dump(),
        )
        events.extend(
            _stun_event(
                player=player,
                room=room,
                target_name=player.name,
                target_payload=player_payload,
                round_id=round_id,
            )
        )
        encounter.pending_player_ability = {}
    else:
        ability_events, ability_result = _execute_pending_player_ability(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            round_id=round_id,
            player_health_max=stats.player_health_max,
        )
        events.extend(ability_events)
        cooldown_exclude = ability_result.cooldown_exclude

        if target_mob.health <= 0:
            return _handle_mob_defeated(
                encounter=encounter,
                player=player,
                target_mob=target_mob,
                room=room,
                events=events,
            )

        if not ability_result.consumed_primary:
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
            events.extend(
                _execute_after_damage_procs(
                    encounter=encounter,
                    player=player,
                    target_mob=target_mob,
                    room=room,
                    actor=player,
                    target=target_mob,
                    result=player_attack,
                    round_id=round_id,
                    attack="attack",
                    label="Attack",
                )
            )

            if target_mob.health <= 0:
                return _handle_mob_defeated(
                    encounter=encounter,
                    player=player,
                    target_mob=target_mob,
                    room=room,
                    events=events,
                )

    if not target_mob.fights_back:
        _advance_non_ticking_effect_durations(encounter)
        cooldowns_changed = _finalize_active_round(
            encounter=encounter,
            player=player,
            cooldown_exclude=cooldown_exclude,
        )
        if cooldown_exclude or cooldowns_changed:
            events.append(ability_state_event(player))
        return CombatStepResult(
            actor_key=player.key,
            events=events,
            encounter_active=True,
        )

    mob_stunned = _consume_stun(
        encounter,
        target_type="mob",
        target_id=target_mob.id,
    )
    if mob_stunned:
        mob_payload = _combat_state_payload(
            serialize_char_from_mob(target_mob).model_dump(),
            target_payload=serialize_char_from_player(player).model_dump(),
        )
        events.extend(
            _stun_event(
                player=player,
                room=room,
                target_name=mob_payload.get("name") or "Something",
                target_payload=mob_payload,
                round_id=round_id,
            )
        )
        _advance_non_ticking_effect_durations(encounter)
        cooldowns_changed = _finalize_active_round(
            encounter=encounter,
            player=player,
            cooldown_exclude=cooldown_exclude,
        )
        if cooldown_exclude or cooldowns_changed:
            events.append(ability_state_event(player))
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
    events.extend(
        _execute_after_damage_procs(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            actor=target_mob,
            target=player,
            result=mob_attack,
            round_id=round_id,
            attack="attack",
            label="Attack",
        )
    )

    if player.health <= 0:
        death_room = config.death_room if config and config.death_room_id else player.get_starting_room()
        player.health = stats.player_health_max
        player.energy = stats.player_energy_max
        player.stamina = stats.player_stamina_max
        player.room = death_room
        # TODO: Apply WR2 death penalties here once the penalty system exists.
        player.save(update_fields=["health", "energy", "stamina", "room"])

        _finish_encounter(encounter)

        affect_data = {
            "actor": serialize_actor(player, death_room).model_dump(),
            "room": _room_payload(player, death_room),
            "origin_room": _room_payload(player, room),
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

    _advance_non_ticking_effect_durations(encounter)
    cooldowns_changed = _finalize_active_round(
        encounter=encounter,
        player=player,
        cooldown_exclude=cooldown_exclude,
    )
    if cooldown_exclude or cooldowns_changed:
        events.append(ability_state_event(player))
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


class FleeAction:
    def execute(self, player_id: int) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            encounter = (
                CombatEncounter.objects.select_for_update()
                .filter(player=player, status=CombatEncounter.STATUS_ACTIVE)
                .first()
            )
            if not encounter:
                raise ActionError("You are not in combat.", code="not_in_combat")

            if player.room_id != encounter.room_id:
                _finish_encounter(encounter)
                raise ActionError("You are no longer in that fight.", code="combat_ended")

            pending = encounter.pending_flee or {}
            if pending.get("status") == "ready" and encounter.resolution_interval == -1:
                step = resolve_combat_encounter_step(encounter.id, auto_advance=False)
                return ActionResult(events=step.events)
            if pending:
                return ActionResult(
                    events=[
                        GameEvent(
                            type="cmd.flee.success",
                            recipients=[player.key],
                            data={"status": pending.get("status", "preparing")},
                            text="You are already trying to flee.",
                        )
                    ]
                )

            destination = _choose_flee_destination(player)
            player.stamina = max(0, int(player.stamina or 0) - destination.movement_cost)
            player.save(update_fields=["stamina"])
            encounter.pending_flee = {
                "status": "preparing",
                "queued_round": int(encounter.round_number or 0),
                "direction": destination.direction,
                "destination_room_id": destination.room_id,
                "movement_cost": destination.movement_cost,
            }
            encounter.pending_player_ability = {}
            encounter.save(update_fields=["pending_flee", "pending_player_ability"])

            events = [
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
                )
            ]

            if encounter.resolution_interval == -1:
                step = resolve_combat_encounter_step(encounter.id, auto_advance=False)
                return ActionResult(events=[*events, *step.events])

            return ActionResult(events=events)


class KillAction:
    @staticmethod
    def _is_implicit_target_candidate(mob: Mob) -> bool:
        return (
            not getattr(mob, "is_pending_deletion", False)
            and getattr(mob, "attackable", True)
            and int(getattr(mob, "health", 0) or 0) > 0
        )

    def _resolve_immediately(self, *, player: Player, target_mob: Mob, config) -> ActionResult:
        events: list[GameEvent] = []
        room = Room.objects.select_related("world", "zone").get(pk=player.room_id)
        stats = _player_combat_stats(player)
        player.health_max = stats.player_health_max
        player.energy_max = stats.player_energy_max
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
                allow_single_match_when_empty=True,
                allow_first_match_when_empty=True,
                empty_candidate_filter=self._is_implicit_target_candidate,
            )
            target_mob = (
                Mob.objects.select_for_update()
                .filter(pk=target_ref.id, is_pending_deletion=False)
                .first()
            )
            if not target_mob:
                raise ActionError("You don't see them here.", code="target_missing")
            if not getattr(target_mob, "attackable", True):
                raise ActionError("You cannot attack them.", code="not_attackable")

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
                stand_player(player)
                return self._resolve_immediately(
                    player=player,
                    target_mob=target_mob,
                    config=config,
                )

            stand_player(player)
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
