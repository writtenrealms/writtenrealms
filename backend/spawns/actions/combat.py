from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import logging
import math
import random
from typing import Any, Iterable

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from config import constants as adv_consts
from core.attack_routines import CombatStrike, resolve_attack_routine
from core.combat_formulas import (
    CombatAttackResult,
    CombatantSnapshot,
    combatant_snapshot,
    resolve_attack,
)
from core.computations import compute_stats
from core.abilities import definition_world
from core.condition_dsl import ConditionContext, evaluate_condition, resolve_path
from core.factions import faction_is_core
from core.leveling import ExperienceGrant, apply_experience
from core.world_config import inherited_system_config
from builders.loot_tables import roll_mob_loot
from builders.models import AbilityDefinition, Currency
from core.economy import economy_world, money_payload
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.effects import (
    active_effect_payload,
    active_combat_effects,
    advance_character_effect_durations,
    build_character_effect,
    clear_actor_effect_cache,
    component_targets_character_effect,
    encounter_effects,
    next_character_effect_tick_ts,
    preventing_action_effect,
    refresh_or_add_character_effect,
)
from spawns.actions.movement_costs import movement_cost
from spawns.actions.targeting import resolve_room_mob_target
from spawns.ability_prepare_state import (
    ability_prepare_state_event,
    ability_prepare_state_events_for_players,
)
from spawns.events import GameEvent, enqueue_game_events
from spawns.models import (
    ActiveEffect,
    CombatEncounter,
    Item,
    Mob,
    Player,
    PlayerCurrencyBalance,
)
from spawns.wallet import WalletError, mutate_balances
from worlds.models import Room


logger = logging.getLogger(__name__)
MAX_AUTO_RESOLVE_ROUNDS = 100
DEFAULT_HIT_MSG_FIRST = "hit"
DEFAULT_HIT_MSG_THIRD = "hits"


def ability_component_overrides(*args, **kwargs):
    from spawns.actions.abilities import ability_component_overrides as fn

    return fn(*args, **kwargs)


def ability_cast_rounds(*args, **kwargs):
    from spawns.actions.abilities import ability_cast_rounds as fn

    return fn(*args, **kwargs)


def ability_is_available_to_player(*args, **kwargs):
    from spawns.actions.abilities import ability_is_available_to_player as fn

    return fn(*args, **kwargs)


def ability_state_event(*args, **kwargs):
    from spawns.actions.abilities import ability_state_event as fn

    return fn(*args, **kwargs)


def cooldown_remaining(*args, **kwargs):
    from spawns.actions.abilities import cooldown_remaining as fn

    return fn(*args, **kwargs)


def decrement_ability_cooldowns(*args, **kwargs):
    from spawns.actions.abilities import decrement_ability_cooldowns as fn

    return fn(*args, **kwargs)


def execute_state_component(*args, **kwargs):
    from spawns.actions.abilities import execute_state_component as fn

    return fn(*args, **kwargs)


def execute_character_effect_component(*args, **kwargs):
    from spawns.actions.abilities import execute_character_effect_component as fn

    return fn(*args, **kwargs)


def pay_ability_cost(*args, **kwargs):
    from spawns.actions.abilities import pay_ability_cost as fn

    return fn(*args, **kwargs)


def player_knows_ability(*args, **kwargs):
    from spawns.actions.abilities import player_knows_ability as fn

    return fn(*args, **kwargs)


def start_ability_cooldown(*args, **kwargs):
    from spawns.actions.abilities import start_ability_cooldown as fn

    return fn(*args, **kwargs)


def stand_player(*args, **kwargs):
    from spawns.actions.player_state import stand_player as fn

    return fn(*args, **kwargs)


def evaluate_movement_policies(*args, **kwargs):
    from spawns.triggers import evaluate_movement_policies as fn

    return fn(*args, **kwargs)


def door_state_lookup(*args, **kwargs):
    from spawns.state_payloads import door_state_lookup as fn

    return fn(*args, **kwargs)


def room_payload_key_for(*args, **kwargs):
    from spawns.state_payloads import room_payload_key_for as fn

    return fn(*args, **kwargs)


def serialize_actor(*args, **kwargs):
    from spawns.state_payloads import serialize_actor as fn

    return fn(*args, **kwargs)


def serialize_char_from_mob(*args, **kwargs):
    from spawns.state_payloads import serialize_char_from_mob as fn

    return fn(*args, **kwargs)


def serialize_char_from_player(*args, **kwargs):
    from spawns.state_payloads import serialize_char_from_player as fn

    return fn(*args, **kwargs)


def serialize_item(*args, **kwargs):
    from spawns.state_payloads import serialize_item as fn

    return fn(*args, **kwargs)


def serialize_room(*args, **kwargs):
    from spawns.state_payloads import serialize_room as fn

    return fn(*args, **kwargs)


def safe_capitalize(*args, **kwargs):
    from spawns.state_payloads import safe_capitalize as fn

    return fn(*args, **kwargs)


@dataclass(frozen=True)
class CombatStats:
    player_health_max: int
    player_energy_max: int
    player_stamina_max: int


def mob_should_aggro_player(mob: Mob, player: Player) -> bool:
    if player.is_invisible:
        return False

    raw_aggression = getattr(mob, "aggression", None)
    if raw_aggression is None:
        return False
    aggression = adv_consts.canonical_mob_aggression(raw_aggression)
    if not aggression:
        return False
    if aggression == adv_consts.MOB_AGGRESSION_ALL:
        return True
    if aggression == adv_consts.MOB_AGGRESSION_PLAYERS:
        return True
    if aggression == adv_consts.MOB_AGGRESSION_PASSIVE:
        return False

    mob_factions = _mob_factions_for_aggro(mob)
    player_factions = dict(getattr(player, "factions", None) or {})
    mob_core = mob_factions.pop("core", None)
    player_core = player_factions.pop("core", None)

    if mob_core is not None and player_core is not None and mob_core != player_core:
        return True

    for faction, standing in mob_factions.items():
        if _safe_int(standing) > 0 and _safe_int(player_factions.get(faction, 0)) < 0:
            return True

    return False


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _mob_factions_for_aggro(mob: Mob) -> dict:
    return _explicit_assignment_factions(mob)


def _explicit_assignment_factions(actor) -> dict:
    prefetched = getattr(actor, "_prefetched_objects_cache", {})
    assignments = prefetched.get("faction_assignments")
    if assignments is None:
        assignments = actor.faction_assignments.select_related("faction").all()

    factions = {}
    for assignment in assignments:
        faction = assignment.faction
        if not faction:
            continue
        if faction_is_core(faction):
            factions["core"] = faction.code
        else:
            factions[faction.code] = assignment.value
    return factions


@dataclass(frozen=True)
class CombatStepResult:
    actor_key: str | None
    events: list[GameEvent]
    encounter_active: bool
    tracker_chase: dict | None = None


def _prepared_player_ability_slug(
    encounter: CombatEncounter | None,
) -> str | None:
    if not encounter or encounter.status != CombatEncounter.STATUS_ACTIVE:
        return None
    pending = encounter.pending_player_ability
    if not isinstance(pending, dict):
        return None
    return str(pending.get("ability") or "").strip().lower() or None


def _with_ability_prepare_transition(
    result: CombatStepResult,
    *,
    player: Player,
    previous_slug: str | None,
    current_slug: str | None,
) -> CombatStepResult:
    if previous_slug == current_slug:
        return result
    state_event = ability_prepare_state_event(player)
    if any(
        event.type == state_event.type
        and player.key in event.recipients
        and event.data == state_event.data
        for event in result.events
    ):
        return result
    return replace(
        result,
        events=[
            *result.events,
            state_event,
        ],
    )


@dataclass(frozen=True)
class PlayerTurnOutcome:
    events: list[GameEvent]
    cooldown_exclude: str | None = None
    target_defeated: bool = False


@dataclass(frozen=True)
class MobTurnOutcome:
    events: list[GameEvent]
    player_defeated: bool = False
    actor_key: str | None = None
    cooldown_exclude: str | None = None


@dataclass(frozen=True)
class StrikeOutcome:
    events: list[GameEvent]
    target_defeated: bool = False


@dataclass(frozen=True)
class EffectAdvanceOutcome:
    events: list[GameEvent]
    defeated_target: Player | Mob | None = None
    killer: Player | Mob | "StoredEffectSource" | None = None
    effects_changed: bool = False


@dataclass(frozen=True)
class StoredEffectSource:
    """Stable attribution and combat stats for an effect whose source vanished."""

    actor_type: str
    id: int
    key: str
    name: str
    level: int
    stats: dict[str, float]
    weapon_damage: float
    is_disarmed: bool
    outgoing_damage_multiplier: float
    world: Any

    @property
    def pk(self) -> int:
        return self.id

    @property
    def active_effects(self) -> list:
        return []

    def combatant_snapshot(self) -> CombatantSnapshot:
        return CombatantSnapshot(
            actor_type=self.actor_type,
            level=self.level,
            stats=self.stats,
            weapon_damage=self.weapon_damage,
            is_disarmed=self.is_disarmed,
            outgoing_damage_multiplier=self.outgoing_damage_multiplier,
        )


@dataclass(frozen=True)
class FleeDestination:
    direction: str
    room_id: int
    movement_cost: int


@dataclass(frozen=True)
class FleeRouteContext:
    room: Room
    door_states: dict[str, str]
    viewed_room_ids: set[int]
    movement_budget: int


@dataclass(frozen=True)
class FleeCompletionOutcome:
    terminal_result: CombatStepResult | None
    events: list[GameEvent]
    player_primary_consumed: bool = False


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


def _combat_room_recipients(room: Room, *, exclude_player_ids: set[int]) -> list[str]:
    queryset = Player.objects.filter(room_id=room.id, in_game=True)
    if exclude_player_ids:
        queryset = queryset.exclude(pk__in=exclude_player_ids)
    return [f"player.{pid}" for pid in queryset.values_list("id", flat=True)]


def _mob_target_priority(mob: Mob | None) -> int:
    if mob is None:
        return 0
    try:
        return int(getattr(mob, "target_priority", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _target_priority_sort_key(mob: Mob) -> tuple[int, int]:
    return (-_mob_target_priority(mob), int(mob.id or 0))


def _encounter_target_priority_sort_key(encounter: CombatEncounter) -> tuple[int, int, int]:
    mob = getattr(encounter, "mob", None)
    mob_id = int(getattr(mob, "id", None) or encounter.mob_id or 0)
    return (
        -int(bool(getattr(encounter, "faceoff_override", False))),
        -_mob_target_priority(mob),
        mob_id,
        int(encounter.id or 0),
    )


def _active_faceoff_encounter_queryset(
    player: Player,
    *,
    room: Room | None = None,
    lock: bool = False,
):
    queryset = (
        CombatEncounter.objects
        .select_related("mob")
        .filter(
            player=player,
            status=CombatEncounter.STATUS_ACTIVE,
            mob_id__isnull=False,
            mob__is_pending_deletion=False,
            mob__health__gt=0,
        )
    )
    if room is not None:
        queryset = queryset.filter(room=room, mob__room=room)
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return queryset


def primary_active_encounter_for_player(
    player: Player,
    *,
    room: Room | None = None,
    lock: bool = False,
) -> CombatEncounter | None:
    encounters = list(_active_faceoff_encounter_queryset(player, room=room, lock=lock))
    if not encounters:
        return None
    return sorted(encounters, key=_encounter_target_priority_sort_key)[0]


def active_player_encounter_for_mob(
    player: Player,
    *,
    mob_id: int,
    room: Room | None = None,
    lock: bool = False,
) -> CombatEncounter | None:
    return (
        _active_faceoff_encounter_queryset(player, room=room, lock=lock)
        .filter(mob_id=mob_id)
        .first()
    )


def set_faceoff_override(encounter: CombatEncounter) -> None:
    if encounter._state.adding:
        encounter.faceoff_override = True
        return

    CombatEncounter.objects.filter(
        player_id=encounter.player_id,
        status=CombatEncounter.STATUS_ACTIVE,
    ).exclude(pk=encounter.pk).update(faceoff_override=False)
    if not encounter.faceoff_override:
        encounter.faceoff_override = True
        encounter.save(update_fields=["faceoff_override"])


def _is_primary_faceoff_encounter(
    *,
    encounter: CombatEncounter,
    player: Player,
    room: Room,
) -> bool:
    primary = primary_active_encounter_for_player(player, room=room)
    return bool(primary and primary.id == encounter.id)


def _encounter_mob_name(encounter: CombatEncounter | None) -> str:
    mob = getattr(encounter, "mob", None)
    if not mob:
        return "them"
    if mob.name:
        return mob.name
    if getattr(mob, "definition", None):
        return mob.definition.name or "them"
    return "them"


def _encounter_actor_ref(actor: Player | Mob, *, side: str) -> dict:
    actor_type = "player" if isinstance(actor, Player) else "mob"
    return {
        "type": actor_type,
        "id": int(actor.id),
        "key": actor.key,
        "side": side,
    }


def _actor_ref_token(ref: dict) -> str:
    return f"{ref.get('type')}:{int(ref.get('id') or 0)}"


def _actor_ref_matches(ref: dict, *, actor_type: str, actor_id: int) -> bool:
    return str(ref.get("type") or "") == actor_type and int(ref.get("id") or 0) == int(actor_id)


def _current_encounter_participants(*, player: Player, target_mob: Mob) -> list[dict]:
    # Near-term WR2 combat is still one player plus one mob. Keep the stored
    # refs typed and side-aware so future CombatParticipant rows can feed this
    # same ordering contract for parties, hostile packs, summons, and hirelings.
    return [
        _encounter_actor_ref(player, side="player_party"),
        _encounter_actor_ref(target_mob, side="hostile"),
    ]


def encounter_opening_priority_ref(actor: Player | Mob, *, side: str, source: str) -> dict:
    ref = _encounter_actor_ref(actor, side=side)
    ref["source"] = source
    return ref


def _roll_initiative_order(participants: list[dict]) -> list[dict]:
    rolls = []
    for participant in participants:
        rolls.append({
            **participant,
            "initiative": random.randint(1, 1_000_000),
            "source": "roll",
        })
    return sorted(
        rolls,
        key=lambda ref: (
            -int(ref.get("initiative") or 0),
            str(ref.get("type") or ""),
            int(ref.get("id") or 0),
        ),
    )


def _valid_initiative_order(order: object, participants: list[dict]) -> bool:
    if not isinstance(order, list) or not order:
        return False
    expected = {_actor_ref_token(ref) for ref in participants}
    actual = {_actor_ref_token(ref) for ref in order if isinstance(ref, dict)}
    return expected.issubset(actual)


def ensure_encounter_initiative_order(
    encounter: CombatEncounter,
    *,
    player: Player,
    target_mob: Mob,
    save: bool = True,
) -> list[dict]:
    participants = _current_encounter_participants(player=player, target_mob=target_mob)
    order = encounter.initiative_order or []
    if not _valid_initiative_order(order, participants):
        order = _roll_initiative_order(participants)
        encounter.initiative_order = order
        if save and not encounter._state.adding:
            encounter.save(update_fields=["initiative_order"])
    return order


def _opening_priority_for_round(encounter: CombatEncounter) -> list[dict]:
    if int(encounter.round_number or 0) != 1:
        return []
    priority = encounter.opening_priority or []
    if not isinstance(priority, list):
        return []
    return [ref for ref in priority if isinstance(ref, dict)]


def _primary_turn_order(
    encounter: CombatEncounter,
    *,
    player: Player,
    target_mob: Mob,
) -> list[dict]:
    base_order = ensure_encounter_initiative_order(
        encounter,
        player=player,
        target_mob=target_mob,
    )
    opening_priority = _opening_priority_for_round(encounter)
    if not opening_priority:
        return base_order

    # Hook for charge/ambush/prepared attacks: populate `opening_priority`
    # before the first round with the actor refs that should override normal
    # initiative for their first primary action only. The persistent initiative
    # order remains unchanged for later rounds.
    prioritized_tokens = [_actor_ref_token(ref) for ref in opening_priority]
    prioritized_token_set = set(prioritized_tokens)
    prioritized = [
        ref
        for token in prioritized_tokens
        for ref in base_order
        if _actor_ref_token(ref) == token
    ]
    remaining = [
        ref
        for ref in base_order
        if _actor_ref_token(ref) not in prioritized_token_set
    ]
    return [*prioritized, *remaining]


def _ensure_corpse(mob: Mob) -> int:
    corpse = mob.inventory.filter(type="corpse").order_by("id").first()
    if corpse:
        return corpse.id

    # TODO: Consider a virtual corpse presentation if we want to stop
    # pre-creating corpse records for every spawned mob.
    return mob.create_corpse().id


def _serialize_corpse(corpse_id: int, *, viewer: Player | None = None) -> dict:
    corpse = Item.objects.select_related("definition", "currency").get(pk=corpse_id)
    return serialize_item(corpse, viewer=viewer, include_inventory=True).model_dump()


def _normalize_hit_message(value: Any, *, default: str) -> str:
    message = str(value or "").strip()
    return message or default


def _basic_attack_hit_messages(
    actor: Player | Mob,
    *,
    weapon_slot: str = adv_consts.EQUIPMENT_SLOT_WEAPON,
) -> tuple[str, str]:
    source: Any = actor
    if isinstance(actor, Player):
        equipment = getattr(actor, "equipment", None)
        if equipment is None:
            source = None
        elif weapon_slot == adv_consts.EQUIPMENT_SLOT_OFFHAND:
            source = getattr(equipment, adv_consts.EQUIPMENT_SLOT_OFFHAND, None)
        else:
            source = getattr(equipment, adv_consts.EQUIPMENT_SLOT_WEAPON, None)

    return (
        _normalize_hit_message(
            getattr(source, "hit_msg_first", None),
            default=DEFAULT_HIT_MSG_FIRST,
        ),
        _normalize_hit_message(
            getattr(source, "hit_msg_third", None),
            default=DEFAULT_HIT_MSG_THIRD,
        ),
    )


def _actor_attack_text(
    target_name: str,
    result: CombatAttackResult,
    *,
    hit_msg_first: str = DEFAULT_HIT_MSG_FIRST,
) -> str:
    if result.outcome == "dodged":
        return f"{safe_capitalize(target_name)} dodges your attack."
    if result.is_crit_hit:
        return (
            f"You critically {hit_msg_first} {target_name} "
            f"for {result.damage_taken} damage."
        )
    return f"You {hit_msg_first} {target_name} for {result.damage_taken} damage."


def _actor_hit_text(
    actor_name: str,
    result: CombatAttackResult,
    *,
    hit_msg_third: str = DEFAULT_HIT_MSG_THIRD,
) -> str:
    if result.outcome == "dodged":
        return f"You dodge {actor_name}'s attack."
    if result.is_crit_hit:
        return (
            f"{safe_capitalize(actor_name)} critically {hit_msg_third} you "
            f"for {result.damage_taken} damage."
        )
    return (
        f"{safe_capitalize(actor_name)} {hit_msg_third} you "
        f"for {result.damage_taken} damage."
    )


def _room_attack_text(
    actor_name: str,
    target_name: str,
    result: CombatAttackResult,
    *,
    hit_msg_third: str = DEFAULT_HIT_MSG_THIRD,
) -> str:
    if result.outcome == "dodged":
        return f"{safe_capitalize(target_name)} dodges {actor_name}'s attack."
    if result.is_crit_hit:
        return (
            f"{safe_capitalize(actor_name)} critically {hit_msg_third} {target_name} "
            f"for {result.damage_taken} damage."
        )
    return (
        f"{safe_capitalize(actor_name)} {hit_msg_third} {target_name} "
        f"for {result.damage_taken} damage."
    )


def _combat_name(actor: Player | Mob | StoredEffectSource) -> str:
    is_player = isinstance(actor, Player) or (
        isinstance(actor, StoredEffectSource) and actor.actor_type == "player"
    )
    fallback = "Someone" if is_player else "Something"
    return str(getattr(actor, "name", "") or fallback).strip()


def _possessive(name: str) -> str:
    name = str(name or "").strip() or "Something"
    suffix = "'" if name.lower().endswith("s") else "'s"
    return f"{name}{suffix}"


def _effect_source_text(
    *,
    source: Player | Mob | StoredEffectSource,
    viewer: Player,
    label: str,
) -> str:
    source_is_player = isinstance(source, Player) or (
        isinstance(source, StoredEffectSource) and source.actor_type == "player"
    )
    if source_is_player and source.pk == viewer.pk:
        return f"your {label}"
    return f"{_possessive(_combat_name(source))} {label}"


def _periodic_damage_text(
    *,
    viewer: Player,
    source: Player | Mob | StoredEffectSource,
    target: Player | Mob,
    label: str,
    result: CombatAttackResult,
) -> str:
    source_text = _effect_source_text(source=source, viewer=viewer, label=label)
    if result.outcome == "dodged":
        if isinstance(target, Player) and target.pk == viewer.pk:
            return f"You avoid {source_text}."
        return f"{safe_capitalize(_combat_name(target))} avoids {source_text}."
    if isinstance(target, Player) and target.pk == viewer.pk:
        return f"You suffer {result.damage_taken} damage from {source_text}."
    return (
        f"{safe_capitalize(_combat_name(target))} suffers "
        f"{result.damage_taken} damage from {source_text}."
    )


def _periodic_damage_room_text(
    *,
    source: Player | Mob | StoredEffectSource,
    target: Player | Mob,
    label: str,
    result: CombatAttackResult,
) -> str:
    source_text = f"{_possessive(_combat_name(source))} {label}"
    target_name = safe_capitalize(_combat_name(target))
    if result.outcome == "dodged":
        return f"{target_name} avoids {source_text}."
    return f"{target_name} suffers {result.damage_taken} damage from {source_text}."


def _effect_application_text(
    *,
    viewer: Player | None,
    actor: Player | Mob,
    target: Player | Mob,
    label: str,
) -> str:
    actor_name = safe_capitalize(_combat_name(actor))
    target_name = _combat_name(target)
    if viewer is not None and isinstance(actor, Player) and actor.pk == viewer.pk:
        if isinstance(target, Player) and target.pk == viewer.pk:
            return f"You apply {label} on yourself."
        return f"You apply {label} on {target_name}."
    if viewer is not None and isinstance(target, Player) and target.pk == viewer.pk:
        return f"{actor_name} applies {label} on you."
    return f"{actor_name} applies {label} on {target_name}."


def _mob_death_text(mob_name: str | None) -> str:
    name = str(mob_name or "").strip() or "Something"
    return f"{safe_capitalize(name)} is dead! R.I.P."


def _reward_text(
    *,
    experience_gained: int = 0,
    currency_rewards: list[dict] | None = None,
    leveling: ExperienceGrant | None = None,
) -> str | None:
    lines: list[str] = []
    if experience_gained > 0:
        lines.append(f"You gain {experience_gained} experience.")
    if leveling and leveling.leveled_up:
        lines.append(f"You are now level {leveling.new_level}!")
    for reward in currency_rewards or []:
        lines.append(f"You receive {reward['display']}.")
    if not lines:
        return None
    return "\n".join(lines)


def _empty_corpse_payload() -> dict:
    return {"key": "", "name": "", "inventory": []}


def _death_notification_recipients(
    *,
    deceased: Player,
    origin_room: Room | None,
    killer=None,
) -> list[str]:
    if not origin_room:
        return []
    qs = Player.objects.filter(room_id=origin_room.id, in_game=True).exclude(pk=deceased.id)
    if isinstance(killer, Player):
        qs = qs.exclude(pk=killer.id)
    return [f"player.{player_id}" for player_id in qs.values_list("id", flat=True)]


def _death_killer_payload(killer) -> dict | None:
    if isinstance(killer, Player):
        return serialize_char_from_player(killer).model_dump()
    if isinstance(killer, Mob):
        return serialize_char_from_mob(killer).model_dump()
    if isinstance(killer, Room):
        return {
            "id": killer.id,
            "key": killer.key,
            "name": killer.name,
            "char_type": "room",
        }
    if isinstance(killer, StoredEffectSource):
        return _stored_effect_source_payload(killer)
    return None


def _equipped_items(player: Player) -> list[tuple[str, Item]]:
    equipment = player.equipment
    if not equipment:
        return []
    items: list[tuple[str, Item]] = []
    seen_ids: set[int] = set()
    for slot in adv_consts.EQUIPMENT_SLOTS:
        item = getattr(equipment, slot, None)
        if item and item.id not in seen_ids:
            items.append((slot, item))
            seen_ids.add(item.id)
    return items


def _clear_equipment_slots(player: Player, slots: list[str]) -> None:
    if not slots:
        return
    equipment = player.equipment
    for slot in slots:
        setattr(equipment, slot, None)
    equipment.save(update_fields=slots)


def _transfer_items_to_container(items: list[Item], container) -> None:
    if not items or container is None:
        return
    container_ct = ContentType.objects.get_for_model(container.__class__)
    Item.objects.filter(pk__in=[item.id for item in items]).update(
        container_type=container_ct,
        container_id=container.id,
    )


def _create_player_corpse(player: Player, room: Room | None) -> Item | None:
    if room is None:
        return None
    return Item.objects.create(
        name=f"the corpse of {player.name}",
        keywords=f"corpse {player.name}",
        room_description=f"The corpse of {player.name} is lying here.",
        type=adv_consts.ITEM_TYPE_CORPSE,
        world=player.world,
        level=player.level,
        is_pickable=False,
        container=room,
    )


def _equipped_item_value(player: Player, *, currency) -> int:
    """Return only repair value denominated in the configured death currency."""
    return sum(
        max(0, int(item.cost or 0))
        for _, item in _equipped_items(player)
        if item.currency_id == currency.pk
    )


def _apply_player_death_penalty(
    *,
    player: Player,
    death_mode: str,
    death_currency,
    death_currency_penalty,
    origin_room: Room | None,
    is_pvp_death: bool,
) -> tuple[str, int | None, dict | None]:
    if death_mode == adv_consts.DEATH_MODE_LOSE_ALL:
        equipped = _equipped_items(player)
        carried_items = list(player.inventory.filter(is_pending_deletion=False))
        corpse = _create_player_corpse(player, origin_room)
        _clear_equipment_slots(player, [slot for slot, _ in equipped])
        if corpse:
            _transfer_items_to_container([item for _, item in equipped] + carried_items, corpse)
        return "Your equipment is left behind.", corpse.id if corpse else None, None

    if death_mode == adv_consts.DEATH_MODE_LOSE_CURRENCY and not is_pvp_death:
        if death_currency is None:
            raise ActionError(
                "This world's death currency is not configured.",
                code="death_currency_missing",
            )
        penalty = int(
            (Decimal(_equipped_item_value(player, currency=death_currency)) * Decimal(
                str(death_currency_penalty or 0))).to_integral_value(
                    rounding=ROUND_HALF_UP))
        balance = (
            PlayerCurrencyBalance.objects.select_for_update()
            .filter(player=player, currency=death_currency)
            .values_list("amount", flat=True)
            .first()
            or 0
        )
        penalty = min(max(0, penalty), int(balance))
        if penalty > 0:
            try:
                mutate_balances(
                    player,
                    {death_currency: -penalty},
                    reason="character.death",
                )
            except WalletError as error:
                raise ActionError(str(error), code=error.code)
            penalty_money = money_payload(penalty, death_currency)
            return f"You pay {penalty_money['display']} for repairs.", None, penalty_money
        return "", None, None

    if death_mode == adv_consts.DEATH_MODE_DESTROY_EQ:
        equipped = _equipped_items(player)
        if equipped:
            item_ids = [item.id for _, item in equipped]
            _clear_equipment_slots(player, [slot for slot, _ in equipped])
            Item.objects.filter(pk__in=item_ids).delete()
        return "Your equipment is destroyed.", None, None

    if death_mode == adv_consts.DEATH_MODE_DESTROY_ALL:
        equipped = _equipped_items(player)
        carried_items = list(player.inventory.all())
        item_ids = [item.id for _, item in equipped] + [item.id for item in carried_items]
        _clear_equipment_slots(player, [slot for slot, _ in equipped])
        if item_ids:
            Item.objects.filter(pk__in=item_ids).delete()
        return "Your equipment and inventory are destroyed.", None, None

    if death_mode == adv_consts.DEATH_MODE_LOSE_INV:
        carried_items = list(player.inventory.filter(is_pending_deletion=False))
        corpse = _create_player_corpse(player, origin_room)
        if corpse:
            _transfer_items_to_container(carried_items, corpse)
        return "Your inventory is left behind.", corpse.id if corpse else None, None

    if death_mode == adv_consts.DEATH_MODE_LOSE_EQ:
        equipped = _equipped_items(player)
        corpse = _create_player_corpse(player, origin_room)
        _clear_equipment_slots(player, [slot for slot, _ in equipped])
        if corpse:
            _transfer_items_to_container([item for _, item in equipped], corpse)
        return "Your equipment is left behind.", corpse.id if corpse else None, None

    return "", None, None


def apply_player_death(
    *,
    player: Player,
    origin_room: Room | None = None,
    killer=None,
    target_text: str | None = None,
    room_text: str | None = None,
    config=None,
) -> tuple[Player, list[GameEvent]]:
    with transaction.atomic():
        updated_player = Player.objects.select_for_update().get(pk=player.pk)
        origin_room = origin_room or updated_player.room
        stats = _player_combat_stats(updated_player)
        death_config = config or updated_player.world.effective_config
        death_room = (
            death_config.death_room
            if death_config and death_config.death_room_id
            else updated_player.get_starting_room()
        )
        if not death_room:
            raise ActionError("There is no death room for this world.", code="no_death_room")

        updated_player.health = stats.player_health_max
        updated_player.energy = stats.player_energy_max
        updated_player.stamina = stats.player_stamina_max
        updated_player.room = death_room
        updated_player.save(update_fields=["health", "energy", "stamina", "room"])
        ActiveEffect.objects.filter(target_player=updated_player).delete()
        clear_actor_effect_cache(updated_player)
        penalty_text, corpse_id, penalty_money = _apply_player_death_penalty(
            player=updated_player,
            death_mode=death_config.death_mode if death_config else adv_consts.DEATH_MODE_LOSE_NONE,
            death_currency=death_config.death_currency if death_config else None,
            death_currency_penalty=(
                death_config.death_currency_penalty if death_config else 0),
            origin_room=origin_room or death_room,
            is_pvp_death=(
                isinstance(killer, Player)
                or (
                    isinstance(killer, StoredEffectSource)
                    and killer.actor_type == "player"
                )
            ),
        )

        active_encounters = CombatEncounter.objects.select_for_update().filter(
            player=updated_player,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        for encounter in active_encounters:
            _finish_encounter(encounter)

    affect_data = {
        "actor": serialize_actor(updated_player, death_room).model_dump(),
        "room": _room_payload(updated_player, death_room),
        "penalty": penalty_money,
        "penalty_text": penalty_text,
    }
    if origin_room:
        affect_data["origin_room"] = _room_payload(updated_player, origin_room)
    killer_payload = _death_killer_payload(killer)
    if killer_payload:
        affect_data["killer"] = killer_payload

    events = [
        GameEvent(
            type="affect.death",
            recipients=[updated_player.key],
            data=affect_data,
            text=target_text or "You have been slain.",
        ),
        ability_prepare_state_event(updated_player),
    ]

    if room_text and not updated_player.is_invisible:
        recipients = _death_notification_recipients(
            deceased=updated_player,
            origin_room=origin_room,
            killer=killer,
        )
        if recipients:
            notification_data = {
                "deceased": serialize_char_from_player(updated_player).model_dump(),
                "corpse": (
                    _serialize_corpse(corpse_id, viewer=None)
                    if corpse_id
                    else _empty_corpse_payload()
                ),
            }
            if killer_payload:
                notification_data["killer"] = killer_payload
            events.append(
                GameEvent(
                    type="notification.death",
                    recipients=recipients,
                    data=notification_data,
                    text=room_text,
                )
            )

    return updated_player, events


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


def _aggro_engage_events(
    *,
    player: Player,
    room: Room,
    mob: Mob,
    primary_mob: Mob | None = None,
    player_char_payload: dict | None = None,
    mob_char_payload: dict | None = None,
    primary_mob_char_payload: dict | None = None,
    room_payload: dict | None = None,
) -> list[GameEvent]:
    serialized_player = (
        player_char_payload
        if player_char_payload is not None
        else serialize_char_from_player(player).model_dump()
    )
    serialized_mob = (
        mob_char_payload
        if mob_char_payload is not None
        else serialize_char_from_mob(mob).model_dump()
    )
    if primary_mob_char_payload is not None:
        serialized_primary_mob = primary_mob_char_payload
    elif primary_mob is None or primary_mob.id == mob.id:
        serialized_primary_mob = serialized_mob
    else:
        serialized_primary_mob = serialize_char_from_mob(primary_mob).model_dump()
    player_payload = _combat_state_payload(
        serialized_player,
        target_payload=serialized_primary_mob,
    )
    target_payload = _combat_state_payload(
        serialized_mob,
        target_payload=serialized_player,
    )
    mob_name = target_payload.get("name") or "Something"
    return [
        GameEvent(
            type="cmd.kill.success",
            recipients=[player.key],
            data={
                "actor": player_payload,
                "target": target_payload,
                "room": (
                    room_payload
                    if room_payload is not None
                    else _room_payload(player, room)
                ),
            },
            text=f"{safe_capitalize(mob_name)} attacks you!",
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


def _combat_effect_application_events(
    *,
    viewer: Player,
    room: Room,
    actor: Player | Mob,
    target: Player | Mob,
    ability: AbilityDefinition,
    effect: str,
    label: str,
    duration_rounds: int,
    round_id: str,
) -> list[GameEvent]:
    actor_payload = _combat_state_payload(
        _combat_actor_payload(actor),
        target_payload=_combat_actor_payload(target),
    )
    target_payload = _combat_state_payload(
        _combat_actor_payload(target),
        target_payload=_combat_actor_payload(actor),
    )
    data = {
        "ability": ability.slug,
        "actor": actor_payload,
        "target": target_payload,
        "effect": effect,
        "label": label,
        "duration_rounds": duration_rounds,
        "round_id": round_id,
    }

    events: list[GameEvent] = []
    direct_player_ids: set[int] = set()
    if isinstance(actor, Player):
        direct_player_ids.add(actor.id)
        events.append(
            GameEvent(
                type="notification.combat.effect",
                recipients=[actor.key],
                data=data,
                text=_effect_application_text(
                    viewer=actor,
                    actor=actor,
                    target=target,
                    label=label,
                ),
            )
        )
    if isinstance(target, Player) and not (
        isinstance(actor, Player) and actor.pk == target.pk
    ):
        direct_player_ids.add(target.id)
        events.append(
            GameEvent(
                type="notification.combat.effect",
                recipients=[target.key],
                data=data,
                text=_effect_application_text(
                    viewer=target,
                    actor=actor,
                    target=target,
                    label=label,
                ),
            )
        )

    if viewer.id not in direct_player_ids and viewer.room_id == room.id:
        direct_player_ids.add(viewer.id)
        events.append(
            GameEvent(
                type="notification.combat.effect",
                recipients=[viewer.key],
                data=data,
                text=_effect_application_text(
                    viewer=None,
                    actor=actor,
                    target=target,
                    label=label,
                ),
            )
        )

    if viewer.is_invisible:
        return events

    recipients = _combat_room_recipients(room, exclude_player_ids=direct_player_ids)
    if recipients:
        events.append(
            GameEvent(
                type="notification.combat.effect",
                recipients=recipients,
                data=data,
                text=_effect_application_text(
                    viewer=None,
                    actor=actor,
                    target=target,
                    label=label,
                ),
            )
        )
    return events


def _finish_encounter(encounter: CombatEncounter) -> None:
    if encounter._state.adding:
        encounter.status = CombatEncounter.STATUS_FINISHED
        encounter.next_resolution_ts = None
        return

    ActiveEffect.objects.filter(
        encounter=encounter,
        scope=ActiveEffect.SCOPE_ENCOUNTER,
    ).delete()
    update_fields: list[str] = []
    if encounter.status != CombatEncounter.STATUS_FINISHED:
        encounter.status = CombatEncounter.STATUS_FINISHED
        update_fields.append("status")
    if encounter.next_resolution_ts is not None:
        encounter.next_resolution_ts = None
        update_fields.append("next_resolution_ts")
    if update_fields:
        encounter.save(update_fields=update_fields)


def _clear_pending_encounter_actions(encounter: CombatEncounter) -> None:
    if encounter._state.adding:
        encounter.pending_flee = {}
        encounter.pending_player_ability = {}
        encounter.pending_mob_ability = {}
        return

    update_fields: list[str] = []
    if encounter.pending_flee:
        encounter.pending_flee = {}
        update_fields.append("pending_flee")
    if encounter.pending_player_ability:
        encounter.pending_player_ability = {}
        update_fields.append("pending_player_ability")
    if encounter.pending_mob_ability:
        encounter.pending_mob_ability = {}
        update_fields.append("pending_mob_ability")
    if update_fields:
        encounter.save(update_fields=update_fields)


def _finish_player_encounters_in_room(*, player: Player, room_id: int) -> None:
    active_encounters = CombatEncounter.objects.select_for_update().filter(
        player=player,
        room_id=room_id,
        status=CombatEncounter.STATUS_ACTIVE,
    )
    for active_encounter in active_encounters:
        _clear_pending_encounter_actions(active_encounter)
        _finish_encounter(active_encounter)


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


def _flee_route_context(
    player: Player,
    *,
    movement_budget: int | None = None,
) -> FleeRouteContext:
    if not player.room_id:
        raise ActionError("You are nowhere. Cannot flee.", code="no_room")

    room = _room_with_exits(player.room_id)
    door_states = door_state_lookup(player.world, [room.id]).get(room.id, {})
    config = inherited_system_config(player.world)
    viewed_room_ids: set[int] = set()
    if config and not config.flee_to_unknown_rooms:
        viewed_room_ids = set(player.viewed_rooms.values_list("id", flat=True))
    return FleeRouteContext(
        room=room,
        door_states=door_states,
        viewed_room_ids=viewed_room_ids,
        movement_budget=(
            int(player.stamina or 0)
            if movement_budget is None
            else max(0, int(movement_budget))
        ),
    )


def _flee_policy_failure(
    *,
    player: Player,
    origin_room: Room,
    destination_room: Room,
    direction: str,
):
    for policy_event in (
        adv_consts.TRIGGER_EVENT_BEFORE_MOVE_EXIT,
        adv_consts.TRIGGER_EVENT_BEFORE_MOVE_ENTER,
    ):
        policy_result = evaluate_movement_policies(
            actor=player,
            event=policy_event,
            direction=direction,
            origin_room_id=origin_room.id,
            destination_room_id=destination_room.id,
            world_id=destination_room.world_id,
        )
        if not policy_result.allowed:
            return policy_result
    return None


def _flee_destination_for_direction(
    player: Player,
    route_context: FleeRouteContext,
    direction: str,
) -> FleeDestination:
    room = route_context.room
    destination = getattr(room, direction, None)
    if not destination:
        raise ActionError("There is nowhere to flee to.", code="no_flee_exit")
    if route_context.door_states.get(direction) in ("closed", "locked"):
        raise ActionError("The way is blocked.", code="closed_door")
    if (
        route_context.viewed_room_ids
        and destination.id not in route_context.viewed_room_ids
    ):
        raise ActionError("There is nowhere to flee to.", code="no_flee_exit")
    if destination.type == adv_consts.ROOM_TYPE_WATER:
        has_boat = player.inventory.filter(is_boat=True).exists()
        if not has_boat:
            raise ActionError("There is nowhere to flee to.", code="no_flee_exit")

    destination_cost = movement_cost(destination)
    if route_context.movement_budget < destination_cost:
        raise ActionError("You are too exhausted to flee.", code="exhausted")

    policy_failure = _flee_policy_failure(
        player=player,
        origin_room=room,
        destination_room=destination,
        direction=direction,
    )
    if policy_failure:
        raise ActionError(
            policy_failure.feedback or "You cannot flee that way.",
            code=policy_failure.code,
            data={"trigger_id": policy_failure.trigger_id},
        )

    return FleeDestination(
        direction=direction,
        room_id=destination.id,
        movement_cost=destination_cost,
    )


def _available_flee_destinations(
    player: Player,
    *,
    route_context: FleeRouteContext | None = None,
) -> list[FleeDestination]:
    route_context = route_context or _flee_route_context(player)

    destinations: list[FleeDestination] = []
    has_unaffordable_destination = False
    first_policy_error: ActionError | None = None
    for direction in adv_consts.DIRECTIONS:
        try:
            destinations.append(
                _flee_destination_for_direction(player, route_context, direction)
            )
        except ActionError as err:
            if err.code == "exhausted":
                has_unaffordable_destination = True
            elif err.code == "policy_blocked" and first_policy_error is None:
                first_policy_error = err

    if not destinations:
        if has_unaffordable_destination:
            raise ActionError("You are too exhausted to flee.", code="exhausted")
        if first_policy_error:
            raise first_policy_error
        raise ActionError("There is nowhere to flee to.", code="no_flee_exit")
    return destinations


def _choose_flee_destination(
    player: Player,
    *,
    route_context: FleeRouteContext | None = None,
) -> FleeDestination:
    return random.choice(
        _available_flee_destinations(player, route_context=route_context)
    )


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


def _flee_completion_error_event(
    player: Player,
    *,
    message: str,
    code: str,
    round_id: str,
    data: dict | None = None,
) -> GameEvent:
    return GameEvent(
        type="cmd.flee.error",
        recipients=[player.key],
        data={
            "error": message,
            "code": code,
            "round_id": round_id,
            **(data or {}),
        },
        text=message,
    )


def _action_prevention_data(prevention: dict, *, action: str) -> dict:
    primitive = prevention.get("primitive") or {}
    data = {
        "action": action,
        "effect": prevention.get("effect"),
        "effect_id": prevention.get("id"),
        "effect_label": prevention.get("label"),
        "effect_scope": prevention.get("scope"),
        "effect_remaining_rounds": prevention.get("remaining_rounds"),
        "effect_duration_rounds": prevention.get("duration_rounds"),
    }
    reason = str(primitive.get("reason") or "").strip()
    if reason:
        data["reason"] = reason
    phase = str(primitive.get("phase") or "").strip()
    if phase:
        data["phase"] = phase
    return data


def _action_prevention_message(prevention: dict, *, action: str) -> str:
    label = str(
        prevention.get("label")
        or prevention.get("effect")
        or "An effect"
    ).strip()
    return f"{label} prevents you from {action}ing."


def _reconciled_flee_stamina(
    player: Player,
    *,
    reserved_cost: int,
    replacement_cost: int,
) -> int:
    reconciled = max(
        0,
        int(player.stamina or 0) + max(0, reserved_cost) - max(0, replacement_cost),
    )
    stamina_max = int(getattr(player, "stamina_max", 0) or 0)
    return min(reconciled, stamina_max) if stamina_max > 0 else reconciled


def _cancel_prevented_flee_completion(
    *,
    encounter: CombatEncounter,
    player: Player,
    reserved_cost: int,
    round_id: str,
    message: str,
    code: str,
    data: dict | None = None,
) -> FleeCompletionOutcome:
    encounter.pending_flee = {}
    encounter.pending_player_ability = {}
    if not encounter._state.adding:
        encounter.save(update_fields=["pending_flee", "pending_player_ability"])

    player.stamina = _reconciled_flee_stamina(
        player,
        reserved_cost=reserved_cost,
        replacement_cost=0,
    )
    player.save(update_fields=["stamina"])
    return FleeCompletionOutcome(
        terminal_result=None,
        events=[
            _flee_completion_error_event(
                player,
                message=message,
                code=code,
                round_id=round_id,
                data=data,
            )
        ],
        player_primary_consumed=True,
    )


def _complete_flee(
    *,
    encounter: CombatEncounter,
    player: Player,
    round_id: str,
) -> FleeCompletionOutcome:
    pending = encounter.pending_flee or {}
    destination_room_id = int(pending.get("destination_room_id") or 0)
    direction = str(pending.get("direction") or "").strip()
    if not destination_room_id or direction not in adv_consts.DIRECTIONS:
        encounter.pending_flee = {}
        if not encounter._state.adding:
            encounter.save(update_fields=["pending_flee"])
        return FleeCompletionOutcome(
            terminal_result=CombatStepResult(
                actor_key=player.key,
                events=[
                    _combat_failure_event(
                        player,
                        "You lose your chance to flee.",
                        code="flee_invalid",
                    )
                ],
                encounter_active=True,
            ),
            events=[],
        )

    reserved_cost = max(0, int(pending.get("movement_cost") or 0))
    prevention = preventing_action_effect(
        player,
        "flee",
        phase="before_action",
    )
    if prevention:
        return _cancel_prevented_flee_completion(
            encounter=encounter,
            player=player,
            reserved_cost=reserved_cost,
            round_id=round_id,
            message=_action_prevention_message(prevention, action="flee"),
            code="action_prevented",
            data=_action_prevention_data(prevention, action="flee"),
        )

    route_context = _flee_route_context(
        player,
        movement_budget=int(player.stamina or 0) + reserved_cost,
    )
    destination: FleeDestination | None = None
    try:
        stored_destination = _flee_destination_for_direction(
            player,
            route_context,
            direction,
        )
        if stored_destination.room_id == destination_room_id:
            destination = stored_destination
    except ActionError:
        pass

    if destination is None:
        try:
            destination = _choose_flee_destination(
                player,
                route_context=route_context,
            )
        except ActionError as err:
            encounter.pending_flee = {}
            encounter.pending_player_ability = {}
            encounter.pending_mob_ability = {}
            if not encounter._state.adding:
                encounter.save(update_fields=[
                    "pending_flee",
                    "pending_player_ability",
                    "pending_mob_ability",
                ])
            player.stamina = _reconciled_flee_stamina(
                player,
                reserved_cost=reserved_cost,
                replacement_cost=0,
            )
            player.save(update_fields=["stamina"])
            return FleeCompletionOutcome(
                terminal_result=CombatStepResult(
                    actor_key=player.key,
                    events=[
                        _flee_completion_error_event(
                            player,
                            message=err.message,
                            code=err.code,
                            round_id=round_id,
                            data=err.data,
                        )
                    ],
                    encounter_active=True,
                ),
                events=[],
            )

    route_changed = (
        destination.direction != direction
        or destination.room_id != destination_room_id
        or destination.movement_cost != reserved_cost
    )
    if route_changed:
        player.stamina = _reconciled_flee_stamina(
            player,
            reserved_cost=reserved_cost,
            replacement_cost=destination.movement_cost,
        )

    direction = destination.direction
    destination_room_id = destination.room_id
    origin_room_id = encounter.room_id
    player.room_id = destination_room_id
    player.last_action_ts = timezone.now()
    player_update_fields = ["room", "last_action_ts"]
    if route_changed:
        player_update_fields.append("stamina")
    player.save(update_fields=player_update_fields)
    player.viewed_rooms.add(destination_room_id)

    from spawns.actions.mob_movement import plan_player_escape

    tracker_plan = plan_player_escape(
        player=player,
        origin_room_id=origin_room_id,
        destination_room_id=destination_room_id,
        direction=direction,
        source="flee",
    )

    encounter.pending_flee = {}
    encounter.pending_player_ability = {}
    encounter.pending_mob_ability = {}
    finished_encounters = list(
        CombatEncounter.objects.filter(
            player=player,
            room_id=origin_room_id,
            status=CombatEncounter.STATUS_ACTIVE,
        ).values_list("id", "mob_id")
    )
    _finish_player_encounters_in_room(player=player, room_id=origin_room_id)
    next_effect_tick = next_character_effect_tick_ts(player.world)
    if not CombatEncounter.objects.filter(
        player=player,
        status=CombatEncounter.STATUS_ACTIVE,
    ).exists():
        ActiveEffect.objects.filter(
            scope=ActiveEffect.SCOPE_CHARACTER,
            target_player=player,
            remaining_rounds__gt=0,
        ).update(next_tick_ts=next_effect_tick)
    for mob_id in {mob_id for _, mob_id in finished_encounters if mob_id}:
        if CombatEncounter.objects.filter(
            mob_id=mob_id,
            status=CombatEncounter.STATUS_ACTIVE,
        ).exists():
            continue
        ActiveEffect.objects.filter(
            scope=ActiveEffect.SCOPE_CHARACTER,
            target_mob_id=mob_id,
            remaining_rounds__gt=0,
        ).update(next_tick_ts=next_effect_tick)
    encounter.status = CombatEncounter.STATUS_FINISHED
    encounter.next_resolution_ts = None

    return FleeCompletionOutcome(
        terminal_result=CombatStepResult(
            actor_key=player.key,
            events=[
                *_flee_success_events(
                    player=player,
                    origin_room_id=origin_room_id,
                    destination_room_id=destination_room_id,
                    direction=direction,
                    movement_cost=destination.movement_cost,
                    round_id=round_id,
                ),
                ability_prepare_state_event(player),
            ],
            encounter_active=False,
            tracker_chase=(
                tracker_plan.action_payload()
                if tracker_plan.tracker_mob_ids
                else None
            ),
        ),
        events=[],
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


def _append_mob_defeat_events(
    *,
    encounter: CombatEncounter | None = None,
    player: Player,
    target_mob: Mob,
    room: Room,
    events: list[GameEvent],
) -> None:
    killer_room = Room.objects.filter(pk=player.room_id).first() if player.room_id else None
    remote_kill = player.room_id != room.id
    corpse_id = _ensure_corpse(target_mob)
    deceased_payload = serialize_char_from_mob(target_mob).model_dump()
    exp_reward = int(target_mob.exp_worth or 0)
    reward_snapshot = dict(target_mob.currency_reward_snapshot or {})
    reward_currencies = {
        currency.code: currency
        for currency in Currency.objects.filter(
            world=economy_world(player.world),
            code__in=reward_snapshot,
        )
    }
    reward_deltas = {
        reward_currencies[code].pk: int(reward_snapshot[code])
        for code in sorted(reward_currencies)
        if int(reward_snapshot[code]) > 0
    }
    finished_encounter_ids: set[int] = set()
    if encounter is not None:
        _finish_encounter(encounter)
        if encounter.id:
            finished_encounter_ids.add(encounter.id)
    active_encounters = CombatEncounter.objects.select_for_update().filter(
        mob=target_mob,
        status=CombatEncounter.STATUS_ACTIVE,
    )
    if finished_encounter_ids:
        active_encounters = active_encounters.exclude(pk__in=finished_encounter_ids)
    other_active_encounters = list(active_encounters)
    for active_encounter in other_active_encounters:
        _finish_encounter(active_encounter)
    events.extend(
        ability_prepare_state_events_for_players(
            active_encounter.player_id
            for active_encounter in other_active_encounters
        )
    )

    from spawns.merchants import deactivate_merchant_runtime

    deactivate_merchant_runtime(target_mob)
    corpse = Item.objects.get(pk=corpse_id)
    roll_mob_loot(
        mob=target_mob,
        corpse=corpse,
        killer=player,
        room=room,
    )
    target_mob.delete()

    reward_update_fields: list[str] = []
    leveling: ExperienceGrant | None = None
    if exp_reward:
        leveling = apply_experience(player, exp_reward)
        reward_update_fields.append("experience")
        if leveling.leveled_up:
            reward_update_fields.append("level")
    if reward_update_fields:
        player.save(update_fields=reward_update_fields)
    if reward_deltas:
        try:
            mutate_balances(
                player,
                reward_deltas,
                reason="mob.kill",
            )
        except WalletError as error:
            raise ActionError(str(error), code=error.code)
    currency_rewards = [
        money_payload(int(reward_snapshot[code]), reward_currencies[code])
        for code in sorted(reward_currencies)
        if int(reward_snapshot[code]) > 0
    ]

    actor_payload = serialize_actor(player, killer_room).model_dump()
    corpse_payload = _serialize_corpse(corpse_id, viewer=player)
    room_payload = _room_payload(player, room)
    death_data = {
        "actor": actor_payload,
        "deceased": deceased_payload,
        "corpse": _empty_corpse_payload() if remote_kill else corpse_payload,
        "experience_gained": exp_reward,
        "currency_rewards": currency_rewards,
    }
    if not remote_kill:
        death_data["killer"] = serialize_char_from_player(player).model_dump()
        death_data["room"] = room_payload
    else:
        death_data["remote"] = True
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
        currency_rewards=currency_rewards,
        leveling=leveling,
    )
    if reward_text:
        reward_data = {
            "actor": actor_payload,
            "source": deceased_payload,
            "experience_gained": exp_reward,
            "currency_rewards": currency_rewards,
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
                "currency_rewards": currency_rewards,
                "levels_gained": leveling.levels_gained if leveling else 0,
            },
        )
    )


def _append_uncredited_mob_defeat_events(
    *,
    target_mob: Mob,
    room: Room,
    killer: StoredEffectSource | Mob | None,
    events: list[GameEvent],
) -> None:
    """Finalize a snapshot-attributed kill when no live player can be rewarded."""
    corpse_id = _ensure_corpse(target_mob)
    deceased_payload = serialize_char_from_mob(target_mob).model_dump()
    active_encounters = list(
        CombatEncounter.objects.select_for_update().filter(
            mob=target_mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
    )
    for active_encounter in active_encounters:
        _finish_encounter(active_encounter)
    events.extend(
        ability_prepare_state_events_for_players(
            active_encounter.player_id
            for active_encounter in active_encounters
        )
    )

    from spawns.merchants import deactivate_merchant_runtime

    deactivate_merchant_runtime(target_mob)
    corpse = Item.objects.get(pk=corpse_id)
    roll_mob_loot(
        mob=target_mob,
        corpse=corpse,
        killer=None,
        room=room,
    )
    target_mob.delete()
    recipients = [
        f"player.{player_id}"
        for player_id in Player.objects.filter(room=room, in_game=True).values_list(
            "id", flat=True
        )
    ]
    if not recipients:
        return
    data = {
        "deceased": deceased_payload,
        "corpse": _serialize_corpse(corpse_id, viewer=None),
    }
    killer_payload = _death_killer_payload(killer)
    if killer_payload:
        data["killer"] = killer_payload
    events.append(
        GameEvent(
            type="notification.death",
            recipients=recipients,
            data=data,
            text=_mob_death_text(deceased_payload.get("name")),
        )
    )


def _handle_mob_defeated(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    events: list[GameEvent],
) -> CombatStepResult:
    _append_mob_defeat_events(
        encounter=encounter,
        player=player,
        target_mob=target_mob,
        room=room,
        events=events,
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
    source_world = definition_world(player.world)
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


def _ability_consumes_primary_action_on_resolve(ability: AbilityDefinition) -> bool:
    return bool(getattr(ability, "consumes_primary_action_on_resolve", True))


def _ability_consumes_primary_action_while_casting(
    ability: AbilityDefinition,
) -> bool:
    return bool(getattr(ability, "consumes_primary_action_while_casting", True))


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
                "consumes_primary_action_on_resolve": (
                    _ability_consumes_primary_action_on_resolve(ability)
                ),
                "consumes_primary_action_while_casting": (
                    _ability_consumes_primary_action_while_casting(ability)
                ),
            },
            "round_id": round_id,
            "rounds_remaining": rounds_remaining,
        },
        text=text,
    )


def _mob_ability_casting_event(
    *,
    player: Player,
    mob: Mob,
    ability: AbilityDefinition,
    round_id: str,
    rounds_remaining: int,
) -> GameEvent:
    mob_name = mob.name or "Something"
    if rounds_remaining > 0:
        text = f"{safe_capitalize(mob_name)} continues charging {ability.name}."
    else:
        text = f"{safe_capitalize(mob_name)} charges {ability.name}."
    return GameEvent(
        type="notification.combat.ability_casting",
        recipients=[player.key],
        data={
            "ability": {
                "slug": ability.slug,
                "name": ability.name,
                "consumes_primary_action_on_resolve": (
                    _ability_consumes_primary_action_on_resolve(ability)
                ),
                "consumes_primary_action_while_casting": (
                    _ability_consumes_primary_action_while_casting(ability)
                ),
            },
            "actor": serialize_char_from_mob(mob).model_dump(),
            "round_id": round_id,
            "rounds_remaining": rounds_remaining,
        },
        text=text,
    )


def _combat_actor_type(actor: Player | Mob) -> str:
    return "player" if isinstance(actor, Player) else "mob"


def _stored_effect_source_payload(source: StoredEffectSource) -> dict:
    health_max = int(source.stats.get("health_max") or 0)
    return {
        "id": source.id,
        "key": source.key,
        "name": source.name,
        "level": source.level,
        "health": 0,
        "health_max": health_max,
        "energy": 0,
        "state": "standing",
        "target": None,
        "keywords": source.name.lower(),
        "keyword": source.name.lower().split()[0] if source.name else "",
        "char_type": source.actor_type,
    }


def _combat_actor_payload(actor: Player | Mob | StoredEffectSource) -> dict:
    if isinstance(actor, Player):
        return serialize_char_from_player(actor).model_dump()
    if isinstance(actor, StoredEffectSource):
        return _stored_effect_source_payload(actor)
    return serialize_char_from_mob(actor).model_dump()


def _condition_actor_data(actor: Player | Mob) -> dict:
    data: dict[str, int | float] = {}
    for resource in ("health", "energy", "stamina"):
        current = int(getattr(actor, resource, 0) or 0)
        maximum = _resource_limit(actor, resource)
        data[resource] = current
        data[f"{resource}_max"] = maximum
        data[f"{resource}_percent"] = int((current / maximum) * 100) if maximum > 0 else 0
    return data


def _ability_condition_context_for_actor(
    *,
    actor: Player | Mob,
    ability: AbilityDefinition,
    room: Room | None,
    viewer: Player | None = None,
) -> ConditionContext:
    current_room = room or getattr(actor, "room", None)
    return ConditionContext(
        actor=actor,
        player=actor if isinstance(actor, Player) else viewer,
        room=current_room,
        zone=getattr(current_room, "zone", None),
        world=getattr(actor, "world", None) or getattr(viewer, "world", None),
        ability=ability,
        actor_data=_condition_actor_data(actor),
    )


def _ability_component_overrides_for_actor(
    component: dict,
    *,
    actor: Player | Mob,
    ability: AbilityDefinition | None,
    room: Room | None = None,
    viewer: Player | None = None,
) -> dict[str, Any]:
    overrides = dict(component.get("overrides") or {})
    if not ability:
        return overrides

    scaling = component.get("scaling") or {}
    if not isinstance(scaling, dict):
        return overrides

    source = str(scaling.get("from") or "").strip()
    if not source:
        return overrides
    raw_value = resolve_path(
        source,
        _ability_condition_context_for_actor(
            actor=actor,
            ability=ability,
            room=room,
            viewer=viewer,
        ),
    )
    try:
        points = float(raw_value or 0)
    except (TypeError, ValueError):
        points = 0.0
    points = max(0.0, points)
    if "max_points" in scaling:
        try:
            points = min(points, max(0.0, float(scaling.get("max_points") or 0)))
        except (TypeError, ValueError):
            pass

    try:
        base_multiplier = float(overrides.get("multiplier", 1))
    except (TypeError, ValueError):
        base_multiplier = 1.0
    try:
        multiplier_per_point = float(scaling.get("multiplier_per_point") or 0)
    except (TypeError, ValueError):
        multiplier_per_point = 0.0
    overrides["multiplier"] = base_multiplier + points * multiplier_per_point
    return overrides


def _mob_ability_cooldowns(mob: Mob) -> dict[str, int]:
    if not isinstance(mob.ability_cooldowns, dict):
        return {}
    cooldowns: dict[str, int] = {}
    for raw_slug, raw_rounds in mob.ability_cooldowns.items():
        slug = str(raw_slug or "").strip().lower()
        if not slug:
            continue
        try:
            rounds = int(raw_rounds or 0)
        except (TypeError, ValueError):
            rounds = 0
        if rounds > 0:
            cooldowns[slug] = rounds
    return cooldowns


def _mob_ability_cooldown_remaining(mob: Mob, ability: AbilityDefinition) -> int:
    return _mob_ability_cooldowns(mob).get(ability.slug, 0)


def _start_mob_ability_cooldown(
    mob: Mob,
    ability: AbilityDefinition,
    *,
    hit_landed: bool = False,
) -> bool:
    rounds = int((ability.cooldown or {}).get("rounds") or 0)
    if rounds <= 0:
        return False
    trigger = str((ability.cooldown or {}).get("trigger") or "on_resolve").strip().lower()
    if trigger == "on_hit" and not hit_landed:
        return False
    cooldowns = _mob_ability_cooldowns(mob)
    cooldowns[ability.slug] = rounds
    mob.ability_cooldowns = cooldowns
    return True


def _decrement_mob_ability_cooldowns(mob: Mob, *, exclude: set[str] | None = None) -> bool:
    exclude = exclude or set()
    cooldowns = _mob_ability_cooldowns(mob)
    if not cooldowns:
        if mob.ability_cooldowns not in ({}, None):
            mob.ability_cooldowns = {}
            return True
        return False

    updated = {
        slug: remaining - 1
        for slug, remaining in cooldowns.items()
        if slug not in exclude and remaining - 1 > 0
    }
    for slug in exclude:
        if slug in cooldowns:
            updated[slug] = cooldowns[slug]
    if updated == mob.ability_cooldowns:
        return False
    mob.ability_cooldowns = updated
    return True


def _mob_ability_resource_amount(mob: Mob, ability: AbilityDefinition) -> tuple[str, int]:
    cost = ability.cost or {}
    if not cost:
        return "", 0
    resource = str(cost.get("resource") or "").strip().lower()
    if resource not in {"health", "energy", "stamina"}:
        return "", 0
    try:
        amount = float(cost.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    calc = str(cost.get("calc") or "fixed").strip().lower()
    if calc in {"percent_max", "percent_base"}:
        amount = _resource_limit(mob, resource) * (amount / 100)
    return resource, max(0, int(amount))


def _mob_can_pay_ability_cost(mob: Mob, ability: AbilityDefinition) -> bool:
    resource, amount = _mob_ability_resource_amount(mob, ability)
    if not resource or amount <= 0:
        return True
    return int(getattr(mob, resource, 0) or 0) >= amount


def _pay_mob_ability_cost(mob: Mob, ability: AbilityDefinition) -> str | None:
    resource, amount = _mob_ability_resource_amount(mob, ability)
    if not resource or amount <= 0:
        return None
    current = int(getattr(mob, resource, 0) or 0)
    if current < amount:
        return None
    setattr(mob, resource, max(0, current - amount))
    return resource


def _ability_definition_for_mob(mob: Mob, slug: str) -> AbilityDefinition | None:
    source_world = definition_world(mob.world)
    return AbilityDefinition.objects.filter(
        world=source_world,
        slug=slug,
        is_active=True,
    ).first()


def _pending_ability_payload(
    *,
    ability: AbilityDefinition,
    command: str,
    target_type: str,
    target_id: int,
    queued_round: int,
) -> dict:
    payload = {
        "ability": ability.slug,
        "command": command,
        "target": {
            "type": target_type,
            "id": target_id,
        },
        "queued_round": queued_round,
    }
    cast_rounds = ability_cast_rounds(ability)
    if cast_rounds > 0:
        payload["status"] = "queued"
        payload["cast_rounds_remaining"] = cast_rounds
    return payload


def _mob_ability_target_ref(
    *,
    ability: AbilityDefinition,
    mob: Mob,
    player: Player,
) -> tuple[str, int]:
    target_type = str((ability.target or {}).get("type") or "hostile").strip().lower()
    if target_type in {"self", "ally"}:
        return "mob", mob.id
    return "player", player.id


def _mob_loadout_entries(mob: Mob) -> list[dict]:
    definition = getattr(mob, "definition", None)
    if not definition or not isinstance(definition.combat_abilities, list):
        return []
    return [
        entry
        for entry in definition.combat_abilities
        if isinstance(entry, dict) and str(entry.get("ability") or "").strip()
    ]


def _mob_loadout_entry_matches(
    *,
    entry: dict,
    mob: Mob,
    player: Player,
    room: Room,
    ability: AbilityDefinition,
) -> bool:
    if _mob_ability_cooldown_remaining(mob, ability) > 0:
        return False
    if not _mob_can_pay_ability_cost(mob, ability):
        return False
    condition = entry.get("when") or {}
    if condition in (None, {}, []):
        return True
    return evaluate_condition(
        condition,
        context=_ability_condition_context_for_actor(
            actor=mob,
            ability=ability,
            room=room,
            viewer=player,
        ),
    )


def _choose_mob_ability(
    *,
    mob: Mob,
    player: Player,
    room: Room,
) -> AbilityDefinition | None:
    entries = _mob_loadout_entries(mob)
    if not entries:
        return None

    source_world = definition_world(mob.world)
    slugs = [
        str(entry.get("ability") or "").strip().lower()
        for entry in entries
        if str(entry.get("ability") or "").strip()
    ]
    abilities_by_slug = {
        ability.slug: ability
        for ability in AbilityDefinition.objects.filter(
            world=source_world,
            slug__in=slugs,
            is_active=True,
        )
    }

    weighted: list[tuple[AbilityDefinition, int]] = []
    for entry in entries:
        slug = str(entry.get("ability") or "").strip().lower()
        ability = abilities_by_slug.get(slug)
        if not ability:
            continue
        if not _mob_loadout_entry_matches(
            entry=entry,
            mob=mob,
            player=player,
            room=room,
            ability=ability,
        ):
            continue
        try:
            chance = max(0, min(100, int(entry.get("chance", 100))))
        except (TypeError, ValueError):
            chance = 100
        if chance <= 0:
            continue
        if chance < 100 and random.randint(1, 100) > chance:
            continue
        try:
            weight = max(1, int(entry.get("weight") or 1))
        except (TypeError, ValueError):
            weight = 1
        weighted.append((ability, weight))

    if not weighted:
        return None
    total = sum(weight for _ability, weight in weighted)
    roll = random.randint(1, total)
    cursor = 0
    for ability, weight in weighted:
        cursor += weight
        if roll <= cursor:
            return ability
    return weighted[-1][0]


def _effect_ref(ref: dict | None) -> str:
    ref = ref or {}
    ref_type = str(ref.get("type") or "").strip()
    ref_id = int(ref.get("id") or 0)
    if not ref_type or not ref_id:
        return ""
    return f"{ref_type}.{ref_id}"


def _actor_ref(actor: Player | Mob) -> str:
    return f"{'player' if isinstance(actor, Player) else 'mob'}.{actor.id}"


def _actor_for_effect_ref(
    ref: dict | None,
    *,
    player: Player,
    target_mob: Mob | None,
) -> Player | Mob | None:
    ref = ref or {}
    ref_type = str(ref.get("type") or "").strip().lower()
    ref_id = int(ref.get("id") or 0)
    if ref_type == "player" and ref_id == player.id:
        return player
    if ref_type == "mob" and target_mob is not None and ref_id == target_mob.id:
        return target_mob
    return None


def _append_effect(
    encounter: CombatEncounter,
    *,
    effect: str,
    source: Player | Mob,
    target: Player | Mob,
    duration_rounds: int,
    label: str,
    category: str = "neutral",
    primitives: list[dict] | None = None,
    tick: dict | None = None,
) -> None:
    component = {
        "effect": effect,
        "category": category,
        "duration": {"rounds": duration_rounds},
        "text": {"label": label},
        "primitives": primitives or [],
        "tick": tick or {},
    }
    payload = build_character_effect(
        component=component,
        source=source,
        target=target,
        started_round=int(encounter.round_number or 0),
    )
    ActiveEffect.objects.create(
        world=encounter.world,
        encounter=encounter,
        source_player=source if isinstance(source, Player) else None,
        source_mob=source if isinstance(source, Mob) else None,
        target_player=target if isinstance(target, Player) else None,
        target_mob=target if isinstance(target, Mob) else None,
        scope=ActiveEffect.SCOPE_ENCOUNTER,
        effect=payload["effect"],
        category=payload["category"],
        label=payload["label"],
        remaining_rounds=payload["remaining_rounds"],
        duration_rounds=payload["duration_rounds"],
        started_round=payload["started_round"],
        primitives=payload["primitives"],
        tick=payload["tick"],
        source_snapshot=payload["source_snapshot"],
        is_hostile=effect == "dot",
    )


def _apply_character_scoped_effect(
    *,
    encounter: CombatEncounter,
    component: dict,
    source: Player | Mob,
    target: Player | Mob,
    viewer: Player,
    room: Room,
    ability: AbilityDefinition,
    round_id: str,
) -> list[GameEvent]:
    effect = build_character_effect(
        component=component,
        source=source,
        target=target,
        round_id=round_id,
        started_round=int(encounter.round_number or 0),
    )
    refresh_or_add_character_effect(
        target,
        effect,
        source=source,
        encounter=encounter,
    )
    return _combat_effect_application_events(
        viewer=viewer,
        room=room,
        actor=source,
        target=target,
        ability=ability,
        effect=str(component.get("effect") or "effect"),
        label=_component_label(component, ability),
        duration_rounds=int(((component.get("duration") or {}).get("rounds")) or 1),
        round_id=round_id,
    )


def _damage_absorb_amount(
    primitive: dict,
    *,
    target: Player | Mob,
    source: Player | Mob | None = None,
) -> int:
    try:
        amount = float(primitive.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if str(primitive.get("calc") or "fixed").strip().lower() == "percent_max":
        amount = _resource_limit(target, "health") * (amount / 100)
    if source is not None:
        snapshot = combatant_snapshot(source, world=getattr(source, "world", None))
        for scaling in primitive.get("scaling") or []:
            stat_key = str(scaling.get("source") or "").strip()
            try:
                multiplier = float(scaling.get("multiplier") or 0)
            except (TypeError, ValueError):
                multiplier = 0.0
            amount += float(snapshot.stats.get(stat_key, 0.0) or 0.0) * multiplier
    return max(0, int(math.ceil(amount)))


def _initialize_effect_primitives(
    primitives: list[dict],
    *,
    target: Player | Mob,
    source: Player | Mob | None,
) -> list[dict]:
    initialized: list[dict] = []
    for primitive in primitives:
        if primitive.get("type") != "damage_absorb":
            initialized.append(primitive)
            continue
        initialized.append(
            {
                **primitive,
                "remaining": _damage_absorb_amount(
                    primitive,
                    target=target,
                    source=source,
                ),
            }
        )
    return initialized


def _consume_stun(
    encounter: CombatEncounter,
    *,
    target_type: str,
    target_id: int,
) -> bool:
    target_filter = (
        {"target_player_id": target_id}
        if target_type == "player"
        else {"target_mob_id": target_id}
    )
    effects = list(
        ActiveEffect.objects.select_for_update().filter(
            effect="stun",
            remaining_rounds__gt=0,
            **target_filter,
        ).filter(
            Q(scope=ActiveEffect.SCOPE_CHARACTER)
            | Q(scope=ActiveEffect.SCOPE_ENCOUNTER, encounter=encounter)
        )
    )
    for effect in effects:
        if effect.remaining_rounds <= 1:
            effect.delete()
        else:
            effect.remaining_rounds -= 1
            effect.rounds_elapsed += 1
            effect.save(update_fields=["remaining_rounds", "rounds_elapsed"])
    return bool(effects)


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
    player_text = (
        "You are stunned and cannot act."
        if target_payload.get("key") == player.key
        else f"{safe_capitalize(target_name)} is stunned and cannot act."
    )
    events = [
        GameEvent(
            type="notification.combat.effect",
            recipients=[player.key],
            data=data,
            text=player_text,
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


def _combat_effect_state_event(player: Player) -> GameEvent:
    return GameEvent(
        type="player.combat_effects.update",
        recipients=[player.key],
        data={
            "target": {"key": player.key},
            "active_effects": active_combat_effects(player),
        },
    )


def _character_effect_state_event(player: Player) -> GameEvent:
    event = ability_state_event(player)
    return replace(
        event,
        data={
            "actor": {
                **serialize_char_from_player(player).model_dump(),
                **event.data["actor"],
            }
        },
    )


def _apply_healing(
    *,
    target: Player | Mob,
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
    target_mob: Mob | None,
) -> Player | Mob | None:
    selector = str(target_selector or "effect.target").strip().lower()
    if selector in {"actor", "self", "effect.source"}:
        return _actor_for_effect_ref(
            effect.get("source"),
            player=player,
            target_mob=target_mob,
        )
    if selector in {"target", "ability.target", "effect.target"}:
        target = effect.get("target") or {}
        if target.get("type") == "player" and int(target.get("id") or 0) == player.id:
            return player
        if (
            target.get("type") == "mob"
            and target_mob is not None
            and int(target.get("id") or 0) == target_mob.id
        ):
            return target_mob
    return None


def _execute_resource_change_primitive(
    *,
    primitive: dict,
    effect: dict,
    player: Player,
    target_mob: Mob | None,
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
    target_mob: Mob | None,
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
        "damage_absorbed": result.damage_absorbed,
        "damage_dealt": result.damage_dealt,
        "damage_type": result.damage_type,
        "outcome": result.outcome,
        "attack": attack,
        "label": label,
        "round_id": round_id,
    }

    events: list[GameEvent] = []
    for effect_row in encounter_effects(encounter):
        effect = active_effect_payload(effect_row)
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


def _damage_type_matches_absorb(primitive: dict, *, damage_type: str) -> bool:
    damage_types = primitive.get("damage_types") or []
    if not damage_types:
        return True
    return str(damage_type or "").strip().lower() in {
        str(item or "").strip().lower()
        for item in damage_types
    }


def _absorb_target_payload(target: Player | Mob) -> dict:
    return (
        serialize_char_from_player(target).model_dump()
        if isinstance(target, Player)
        else serialize_char_from_mob(target).model_dump()
    )


def _apply_damage_absorption(
    *,
    encounter: CombatEncounter | None,
    player: Player,
    target_mob: Mob | None,
    target: Player | Mob,
    result: CombatAttackResult,
    round_id: str,
) -> tuple[CombatAttackResult, list[GameEvent]]:
    if result.damage_taken <= 0:
        return result, []

    target_type = "player" if isinstance(target, Player) else "mob"
    target_id = int(target.id)
    remaining_damage = int(result.damage_taken)
    absorbed_total = 0
    events: list[GameEvent] = []
    target_filter = (
        {"target_player_id": target_id}
        if target_type == "player"
        else {"target_mob_id": target_id}
    )
    scope_filter = Q(scope=ActiveEffect.SCOPE_CHARACTER)
    if encounter is not None:
        scope_filter |= Q(scope=ActiveEffect.SCOPE_ENCOUNTER, encounter=encounter)
    effect_rows = ActiveEffect.objects.select_for_update().filter(
        scope_filter,
        remaining_rounds__gt=0,
        **target_filter,
    )

    for effect_row in effect_rows:
        effect = active_effect_payload(effect_row)

        primitives = list(effect.get("primitives") or [])
        if not primitives:
            continue

        next_primitives: list[dict] = []
        had_absorb = False
        absorbed_for_effect = 0
        for primitive in primitives:
            if primitive.get("type") != "damage_absorb":
                next_primitives.append(primitive)
                continue

            had_absorb = True
            if remaining_damage <= 0 or not _damage_type_matches_absorb(
                primitive,
                damage_type=result.damage_type,
            ):
                next_primitives.append(primitive)
                continue

            remaining_absorb = int(
                primitive.get(
                    "remaining",
                    _damage_absorb_amount(
                        primitive,
                        target=target,
                        source=_actor_for_effect_ref(
                            effect.get("source"),
                            player=player,
                            target_mob=target_mob,
                        ),
                    ),
                )
                or 0
            )
            absorbed = min(remaining_damage, max(0, remaining_absorb))
            if absorbed <= 0:
                continue

            absorbed_for_effect += absorbed
            absorbed_total += absorbed
            remaining_damage -= absorbed
            remaining_absorb -= absorbed
            if remaining_absorb > 0:
                next_primitives.append({**primitive, "remaining": remaining_absorb})

        has_remaining_absorb = any(
            primitive.get("type") == "damage_absorb"
            for primitive in next_primitives
        )
        if had_absorb:
            if has_remaining_absorb:
                effect_row.primitives = next_primitives
                effect_row.save(update_fields=["primitives"])
            else:
                effect_row.delete()
            clear_actor_effect_cache(target)

        if absorbed_for_effect > 0:
            effect_key = str(effect.get("effect") or "").strip()
            label = str(effect.get("label") or effect_key or "Barrier").strip()
            events.append(
                GameEvent(
                    type="notification.combat.effect",
                    recipients=[player.key],
                    data={
                        "effect": effect_key,
                        "label": label,
                        "target": _absorb_target_payload(target),
                        "amount": absorbed_for_effect,
                        "remaining": sum(
                            max(0, int(primitive.get("remaining") or 0))
                            for primitive in next_primitives
                            if primitive.get("type") == "damage_absorb"
                        ),
                        "damage_type": result.damage_type,
                        "round_id": round_id,
                    },
                    text=f"{label} absorbs {absorbed_for_effect} damage.",
                )
            )

    if absorbed_total <= 0:
        return result, events
    return (
        replace(
            result,
            damage_taken=max(0, remaining_damage),
            damage_absorbed=int(result.damage_absorbed or 0) + absorbed_total,
        ),
        events,
    )


def _serialize_combat_char(actor: Player | Mob) -> dict:
    if isinstance(actor, Player):
        return serialize_char_from_player(actor).model_dump()
    return serialize_char_from_mob(actor).model_dump()


def _apply_combat_strike(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    actor: Player | Mob,
    target: Player | Mob,
    strike: CombatStrike,
    round_id: str,
) -> StrikeOutcome:
    result = resolve_attack(
        actor=actor,
        target=target,
        world=player.world,
        weapon_slot=strike.weapon_slot,
        damage_multiplier=strike.damage_multiplier,
    )
    result, absorb_events = _apply_damage_absorption(
        encounter=encounter,
        player=player,
        target_mob=target_mob,
        target=target,
        result=result,
        round_id=round_id,
    )
    events = list(absorb_events)

    if result.damage_taken > 0:
        target.health = max(0, int(target.health or 0) - result.damage_taken)
        target.save(update_fields=["health"])

    actor_base = _serialize_combat_char(actor)
    target_base = _serialize_combat_char(target)
    actor_payload = _combat_state_payload(actor_base, target_payload=target_base)
    target_payload = _combat_state_payload(target_base, target_payload=actor_base)

    actor_name = actor_payload.get("name") or "Something"
    target_name = target_payload.get("name") or "them"
    hit_msg_first, hit_msg_third = _basic_attack_hit_messages(
        actor,
        weapon_slot=strike.weapon_slot,
    )
    if isinstance(actor, Player):
        actor_text = _actor_attack_text(
            target_name,
            result,
            hit_msg_first=hit_msg_first,
        )
    else:
        actor_text = _actor_hit_text(
            actor_name,
            result,
            hit_msg_third=hit_msg_third,
        )

    events.extend(
        _combat_attack_events(
            viewer=player,
            room=room,
            actor_payload=actor_payload,
            target_payload=target_payload,
            result=result,
            round_id=round_id,
            actor_text=actor_text,
            room_text=_room_attack_text(
                actor_name,
                target_name,
                result,
                hit_msg_third=hit_msg_third,
            ),
            attack=strike.attack,
            label=strike.label,
        )
    )
    events.extend(
        _execute_after_damage_procs(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            actor=actor,
            target=target,
            result=result,
            round_id=round_id,
            attack=strike.attack,
            label=strike.label,
        )
    )
    return StrikeOutcome(
        events=events,
        target_defeated=int(getattr(target, "health", 0) or 0) <= 0,
    )


def _execute_output_component(
    *,
    encounter: CombatEncounter | None,
    player: Player,
    target_mob: Mob | None,
    room: Room,
    component: dict,
    ability: AbilityDefinition | None,
    round_id: str,
    player_health_max: int,
    actor: Player | Mob | StoredEffectSource | None = None,
    target: Player | Mob | None = None,
    periodic_effect: dict | None = None,
    actor_snapshot: CombatantSnapshot | None = None,
) -> tuple[list[GameEvent], bool]:
    component_type = component.get("type")
    label = _component_label(component, ability)
    events: list[GameEvent] = []
    actor = actor or player
    if target is None:
        if target_mob is None and component_type != "healing":
            return [], False
        target = player if component_type == "healing" else target_mob

    if component_type == "healing":
        result = resolve_attack(
            actor=actor,
            target=target,
            world=player.world,
            profile_key=component.get("profile"),
            overrides=_ability_component_overrides_for_actor(
                component,
                actor=actor,
                ability=ability,
                room=room,
                viewer=player,
            ),
            actor_snapshot=actor_snapshot,
        )
        _apply_healing(
            result=result,
            target=target,
            health_max=player_health_max if isinstance(target, Player) else _resource_limit(target, "health"),
        )
        actor_payload = _combat_state_payload(
            _combat_actor_payload(actor),
            target_payload=_combat_actor_payload(target),
        )
        target_payload = _combat_state_payload(
            _combat_actor_payload(target),
            target_payload=_combat_actor_payload(actor),
        )
        if isinstance(actor, Player) and isinstance(target, Player) and actor.pk == target.pk:
            actor_text = f"You use {label} and heal for {result.healing_done}."
        elif isinstance(actor, Mob) and isinstance(target, Mob) and actor.pk == target.pk:
            actor_text = f"{safe_capitalize(actor.name or 'Something')} uses {label} and heals for {result.healing_done}."
        else:
            actor_text = f"{actor_payload.get('name') or 'Something'} uses {label}."
        events.extend(
            _combat_attack_events(
                viewer=player,
                room=room,
                actor_payload=actor_payload,
                target_payload=target_payload,
                result=result,
                round_id=round_id,
                actor_text=actor_text,
                room_text=f"{actor_payload.get('name') or 'Something'} uses {label}.",
                attack=ability.slug if ability else "effect",
                label=label,
            )
        )
        return events, result.healing_done > 0

    result = resolve_attack(
        actor=actor,
        target=target,
        world=player.world,
        profile_key=component.get("profile"),
        overrides=_ability_component_overrides_for_actor(
            component,
            actor=actor,
            ability=ability,
            room=room,
            viewer=player,
        ),
        actor_snapshot=actor_snapshot,
    )
    result, absorb_events = _apply_damage_absorption(
        encounter=encounter,
        player=player,
        target_mob=target_mob,
        target=target,
        result=result,
        round_id=round_id,
    )
    events.extend(absorb_events)
    if result.damage_taken > 0:
        target.health = max(0, int(target.health or 0) - result.damage_taken)
        target.save(update_fields=["health"])

    actor_char = _combat_state_payload(
        _combat_actor_payload(actor),
        target_payload=_combat_actor_payload(target),
    )
    target_char = _combat_state_payload(
        _combat_actor_payload(target),
        target_payload=_combat_actor_payload(actor),
    )
    actor_name = actor_char.get("name") or "Something"
    target_name = target_char.get("name") or "them"
    if periodic_effect:
        actor_text = _periodic_damage_text(
            viewer=player,
            source=actor,
            target=target,
            label=label,
            result=result,
        )
        room_text = _periodic_damage_room_text(
            source=actor,
            target=target,
            label=label,
            result=result,
        )
    elif isinstance(actor, Player):
        room_text = _room_attack_text(actor_name, target_name, result)
        if result.outcome == "dodged":
            actor_text = f"{target_name} dodges {label}."
        elif result.is_crit_hit:
            actor_text = f"You critically hit {target_name} with {label} for {result.damage_taken} damage."
        else:
            actor_text = f"You hit {target_name} with {label} for {result.damage_taken} damage."
    else:
        room_text = _room_attack_text(actor_name, target_name, result)
        if result.outcome == "dodged":
            actor_text = f"You dodge {label}."
        elif result.is_crit_hit:
            actor_text = f"{safe_capitalize(actor_name)} critically hits you with {label} for {result.damage_taken} damage."
        else:
            actor_text = f"{safe_capitalize(actor_name)} hits you with {label} for {result.damage_taken} damage."
    events.extend(
        _combat_attack_events(
            viewer=player,
            room=room,
            actor_payload=actor_char,
            target_payload=target_char,
            result=result,
            round_id=round_id,
            actor_text=actor_text,
            room_text=room_text,
            attack=ability.slug if ability else "effect",
            label=label,
        )
    )
    if encounter is not None:
        events.extend(
            _execute_after_damage_procs(
                encounter=encounter,
                player=player,
                target_mob=target_mob,
                room=room,
                actor=actor,
                target=target,
                result=result,
                round_id=round_id,
                attack=ability.slug if ability else "effect",
                label=label,
            )
        )
    return events, result.outcome != "dodged" and result.damage_taken > 0


def _secondary_hostile_target(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
) -> Mob | None:
    encounters = sorted(
        _active_faceoff_encounter_queryset(player, room=room, lock=True).exclude(
            pk=encounter.pk,
            mob_id=target_mob.id,
        ),
        key=_encounter_target_priority_sort_key,
    )
    for secondary_encounter in encounters:
        mob = (
            Mob.objects.select_for_update()
            .filter(
                pk=secondary_encounter.mob_id,
                room=room,
                is_pending_deletion=False,
                health__gt=0,
            )
            .first()
        )
        if mob and getattr(mob, "attackable", True):
            return mob
    return None


def _combat_strike_target(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    actor: Player | Mob,
    strike: CombatStrike,
    default_target: Player | Mob,
) -> Player | Mob | None:
    selector = str(getattr(strike, "target", "target") or "target").strip().lower()
    if selector == "room.secondary_hostile":
        if not isinstance(actor, Player):
            return None
        return _secondary_hostile_target(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
        )
    return default_target


def _effect_source_actor(effect: ActiveEffect) -> Player | Mob | None:
    return effect.source_player or effect.source_mob


def _stored_effect_source(effect: ActiveEffect) -> StoredEffectSource | None:
    snapshot = effect.source_snapshot if isinstance(effect.source_snapshot, dict) else {}
    ref = snapshot.get("ref") if isinstance(snapshot.get("ref"), dict) else {}
    actor_type = str(snapshot.get("actor_type") or ref.get("type") or "").strip().lower()
    if actor_type not in {"player", "mob"}:
        return None
    try:
        actor_id = max(0, int(ref.get("id") or 0))
        level = max(1, int(snapshot.get("level") or 1))
        weapon_damage = max(0.0, float(snapshot.get("weapon_damage") or 0))
        outgoing_multiplier = max(
            0.0,
            float(snapshot.get("outgoing_damage_multiplier", 1) or 0),
        )
    except (TypeError, ValueError):
        return None
    stats = snapshot.get("stats") if isinstance(snapshot.get("stats"), dict) else {}
    normalized_stats: dict[str, float] = {}
    for key, value in stats.items():
        try:
            normalized_stats[str(key)] = float(value or 0)
        except (TypeError, ValueError):
            continue
    return StoredEffectSource(
        actor_type=actor_type,
        id=actor_id,
        key=str(snapshot.get("key") or f"{actor_type}.{actor_id}"),
        name=str(snapshot.get("name") or ("Someone" if actor_type == "player" else "Something")),
        level=level,
        stats=normalized_stats,
        weapon_damage=weapon_damage,
        is_disarmed=bool(snapshot.get("is_disarmed", False)),
        outgoing_damage_multiplier=outgoing_multiplier,
        world=effect.world,
    )


def _effect_source_combatant_snapshot(
    effect: ActiveEffect,
    *,
    live_source: Player | Mob | None,
) -> CombatantSnapshot | None:
    stored_source = _stored_effect_source(effect)
    if stored_source is None:
        return None
    # Legacy rows may only have identity metadata. Keep their live-source
    # calculation until a complete snapshot is available.
    if not stored_source.stats and live_source is not None:
        return None
    return stored_source.combatant_snapshot()


def _effect_target_actor(effect: ActiveEffect) -> Player | Mob | None:
    return effect.target_player or effect.target_mob


def _periodic_tick_events(
    events: list[GameEvent],
    *,
    source: Player | Mob | StoredEffectSource,
    target: Player | Mob,
    room: Room,
    detached: bool,
) -> list[GameEvent]:
    source_is_remote_player = (
        isinstance(source, Player) and source.room_id != room.id
    )
    if not detached and not source_is_remote_player:
        return events
    remote_events: list[GameEvent] = []
    for event in events:
        convert = event.type == "notification.combat.attack" and (
            detached or list(event.recipients) == [source.key]
        )
        if convert:
            remote_events.append(
                replace(
                    event,
                    type="notification.combat.effect",
                    data={
                        **event.data,
                        "actor": _combat_actor_payload(source),
                        "target": _combat_actor_payload(target),
                        "remote": detached or source_is_remote_player,
                    },
                )
            )
        else:
            remote_events.append(event)
    return remote_events


def _advance_character_periodic_effects(
    *,
    target_player: Player | None,
    target_mob: Mob | None,
    encounter: CombatEncounter | None,
    viewer: Player | None,
    round_id: str,
    due_at=None,
    advance_player_character_effects: bool = True,
    locked_source_player_ids: set[int] | None = None,
) -> EffectAdvanceOutcome:
    target_filter = Q()
    if target_player is not None:
        target_filter |= Q(target_player=target_player)
    if target_mob is not None:
        target_filter |= Q(target_mob=target_mob)
    if not target_filter:
        return EffectAdvanceOutcome(events=[])

    scope_filter = Q(scope=ActiveEffect.SCOPE_CHARACTER)
    if encounter is not None:
        scope_filter |= Q(scope=ActiveEffect.SCOPE_ENCOUNTER, encounter=encounter)
    effect_queryset = (
        ActiveEffect.objects.select_for_update(of=("self",))
        .filter(
            target_filter,
            remaining_rounds__gt=0,
        )
        .filter(scope_filter)
        .exclude(tick={})
        .select_related(
            "source_player",
            "source_mob",
            "target_player",
            "target_mob",
            "world",
        )
        .order_by("id")
    )
    if encounter is None:
        effect_queryset = effect_queryset.filter(next_tick_ts__lte=due_at or timezone.now())
    effects = list(effect_queryset)
    events: list[GameEvent] = []
    effects_changed = False
    for effect_row in effects:
        if (
            locked_source_player_ids is not None
            and effect_row.source_player_id
            and effect_row.source_player_id not in locked_source_player_ids
        ):
            # The effect was created after this round established its global
            # player lock set. Leave it for the next target-owned pulse.
            continue
        if (
            effect_row.scope == ActiveEffect.SCOPE_CHARACTER
            and effect_row.target_player_id
            and not advance_player_character_effects
        ):
            continue
        if target_player is not None and effect_row.target_player_id == target_player.id:
            target = target_player
        elif target_mob is not None and effect_row.target_mob_id == target_mob.id:
            target = target_mob
        else:
            target = _effect_target_actor(effect_row)
        if isinstance(viewer, Player) and effect_row.source_player_id == viewer.id:
            live_source = viewer
        elif target_player is not None and effect_row.source_player_id == target_player.id:
            live_source = target_player
        elif target_mob is not None and effect_row.source_mob_id == target_mob.id:
            live_source = target_mob
        else:
            live_source = _effect_source_actor(effect_row)
        source = live_source or _stored_effect_source(effect_row)
        source_combatant_snapshot = _effect_source_combatant_snapshot(
            effect_row,
            live_source=live_source,
        )
        if target is None:
            effect_row.delete()
            effects_changed = True
            continue
        tick_token = f"{round_id}:effect:{effect_row.id}"
        if effect_row.last_tick_token == tick_token:
            continue
        if effect_row.started_round_id == round_id:
            continue

        elapsed = effect_row.rounds_elapsed + 1
        remaining = effect_row.remaining_rounds - 1
        tick = effect_row.tick or {}
        every = max(1, int(tick.get("every_rounds") or 1))
        if source is not None and elapsed % every == 0:
            effect_payload = active_effect_payload(effect_row)
            tick_viewer = viewer
            if isinstance(target, Player):
                tick_viewer = target
            elif isinstance(source, Player):
                tick_viewer = source
            room = target.room
            pair_mob = target if isinstance(target, Mob) else source if isinstance(source, Mob) else None
            tick_primitives = tick.get("primitives") or []
            if tick_viewer is not None:
                if tick_primitives:
                    effect_events = _execute_effect_primitives(
                        primitives=tick_primitives,
                        effect=effect_payload,
                        player=tick_viewer,
                        target_mob=pair_mob,
                        room=room,
                        round_id=round_id,
                    )
                else:
                    component = tick.get("component") or {}
                    before_health = int(getattr(target, "health", 0) or 0)
                    effect_events, _ = _execute_output_component(
                        encounter=encounter,
                        player=tick_viewer,
                        target_mob=pair_mob,
                        room=room,
                        component=component,
                        ability=None,
                        round_id=round_id,
                        player_health_max=(
                            _player_combat_stats(target).player_health_max
                            if isinstance(target, Player)
                            else _resource_limit(target, "health")
                        ),
                        actor=source,
                        target=target,
                        periodic_effect=effect_payload,
                        actor_snapshot=source_combatant_snapshot,
                    )
                    if component.get("type") == "healing" and int(target.health or 0) != before_health:
                        target.save(update_fields=["health"])
                events.extend(
                    _periodic_tick_events(
                        effect_events,
                        source=source,
                        target=target,
                        room=room,
                        detached=encounter is None,
                    )
                )
            elif not tick_primitives:
                component = tick.get("component") or {}
                result = resolve_attack(
                    actor=source,
                    target=target,
                    world=effect_row.world,
                    profile_key=component.get("profile"),
                    overrides=component.get("overrides") or {},
                    actor_snapshot=source_combatant_snapshot,
                )
                if component.get("type") == "healing":
                    target.health = min(
                        _resource_limit(target, "health"),
                        int(target.health or 0) + result.healing_done,
                    )
                else:
                    target.health = max(0, int(target.health or 0) - result.damage_taken)
                target.save(update_fields=["health"])

        if remaining > 0:
            effect_row.remaining_rounds = remaining
            effect_row.rounds_elapsed = elapsed
            effect_row.last_tick_ts = timezone.now()
            effect_row.last_tick_token = tick_token
            effect_row.next_tick_ts = next_character_effect_tick_ts(effect_row.world)
            effect_row.save(
                update_fields=[
                    "remaining_rounds",
                    "rounds_elapsed",
                    "last_tick_ts",
                    "last_tick_token",
                    "next_tick_ts",
                ]
            )
        else:
            effect_row.delete()
        effects_changed = True
        clear_actor_effect_cache(target)

        if int(getattr(target, "health", 0) or 0) <= 0:
            return EffectAdvanceOutcome(
                events=events,
                defeated_target=target,
                killer=source,
                effects_changed=effects_changed,
            )

    return EffectAdvanceOutcome(events=events, effects_changed=effects_changed)


def _resolve_detached_actor_effects(
    *,
    target_type: str,
    target_id: int,
    due_at,
) -> list[GameEvent]:
    target_filter = (
        {"target_player_id": target_id}
        if target_type == "player"
        else {"target_mob_id": target_id}
    )
    candidate_effects = ActiveEffect.objects.filter(
        scope=ActiveEffect.SCOPE_CHARACTER,
        remaining_rounds__gt=0,
        next_tick_ts__lte=due_at,
        **target_filter,
    )
    player_ids = set(
        candidate_effects.filter(source_player_id__isnull=False).values_list(
            "source_player_id", flat=True
        )
    )
    mob_ids = set(
        candidate_effects.filter(source_mob_id__isnull=False).values_list(
            "source_mob_id", flat=True
        )
    )
    if target_type == "player":
        player_ids.add(target_id)
    else:
        mob_ids.add(target_id)

    locked_players = {
        actor.id: actor
        for actor in Player.objects.select_for_update().filter(id__in=sorted(player_ids)).order_by("id")
    }
    locked_mobs = {
        actor.id: actor
        for actor in Mob.objects.select_for_update().filter(id__in=sorted(mob_ids)).order_by("id")
    }
    target_player = locked_players.get(target_id) if target_type == "player" else None
    target_mob = locked_mobs.get(target_id) if target_type == "mob" else None
    target = target_player or target_mob
    if target is None:
        return []
    if target.world.lifecycle != adv_consts.WORLD_LIFECYCLE_RUNNING:
        return []
    if isinstance(target, Player) and (not target.in_game or target.room_id is None):
        return []
    if isinstance(target, Mob) and (
        target.is_pending_deletion or target.health <= 0 or target.room_id is None
    ):
        return []

    active_encounter_filter = Q(status=CombatEncounter.STATUS_ACTIVE)
    if isinstance(target, Player):
        active_encounter_filter &= Q(player=target)
    else:
        active_encounter_filter &= Q(mob=target)
    if CombatEncounter.objects.filter(active_encounter_filter).exists():
        return []

    pulse_id = f"effect-pulse:{target_type}:{target_id}:{int(due_at.timestamp())}"
    viewer = target_player or next(iter(locked_players.values()), None)
    outcome = _advance_character_periodic_effects(
        target_player=target_player,
        target_mob=target_mob,
        encounter=None,
        viewer=viewer,
        round_id=pulse_id,
        due_at=due_at,
    )
    durations_changed = advance_character_effect_durations(
        target,
        current_round_id=pulse_id,
        due_at=due_at,
    )
    events = list(outcome.events)
    if isinstance(outcome.defeated_target, Mob):
        if isinstance(outcome.killer, Player):
            death_room = outcome.defeated_target.room
            _append_mob_defeat_events(
                player=outcome.killer,
                target_mob=outcome.defeated_target,
                room=death_room,
                events=events,
            )
        else:
            _append_uncredited_mob_defeat_events(
                target_mob=outcome.defeated_target,
                room=outcome.defeated_target.room,
                killer=outcome.killer,
                events=events,
            )
        return events
    if isinstance(outcome.defeated_target, Player):
        killer = outcome.killer
        killer_name = _combat_name(killer) if killer is not None else "An effect"
        updated_player, death_events = apply_player_death(
            player=outcome.defeated_target,
            origin_room=outcome.defeated_target.room,
            killer=killer,
            target_text="You succumb to your wounds.",
            room_text=f"{killer_name} kills {outcome.defeated_target.name}.",
        )
        events.extend(death_events)
        clear_actor_effect_cache(updated_player)
        return events
    if isinstance(target, Player) and (outcome.effects_changed or durations_changed):
        events.append(_character_effect_state_event(target))
    return events


def resolve_due_character_effects(
    *,
    due_at=None,
    limit: int = 200,
    world_ids: Iterable[int] | None = None,
    persist_events: bool = False,
) -> list[GameEvent]:
    due_at = due_at or timezone.now()
    due_effects = ActiveEffect.objects.filter(
        scope=ActiveEffect.SCOPE_CHARACTER,
        remaining_rounds__gt=0,
        next_tick_ts__lte=due_at,
        world__lifecycle=adv_consts.WORLD_LIFECYCLE_RUNNING,
    ).filter(
        Q(
            target_player__in_game=True,
            target_player__room_id__isnull=False,
        )
        | Q(
            target_mob__is_pending_deletion=False,
            target_mob__health__gt=0,
            target_mob__room_id__isnull=False,
        )
    )
    if world_ids is not None:
        world_ids = list(world_ids)
        if not world_ids:
            return []
        due_effects = due_effects.filter(world_id__in=world_ids)
    target_refs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    ordered_targets = due_effects.order_by("next_tick_ts", "id").values_list(
        "target_player_id",
        "target_mob_id",
    )
    target_limit = max(1, int(limit or 1))
    for player_id, mob_id in ordered_targets.iterator(chunk_size=200):
        ref = ("player", int(player_id)) if player_id else ("mob", int(mob_id))
        if ref not in seen:
            seen.add(ref)
            target_refs.append(ref)
            if len(target_refs) >= target_limit:
                break

    events: list[GameEvent] = []
    for target_type, target_id in target_refs:
        with transaction.atomic():
            target_events = _resolve_detached_actor_effects(
                target_type=target_type,
                target_id=target_id,
                due_at=due_at,
            )
            if persist_events:
                enqueue_game_events(target_events)
            events.extend(target_events)
    return events


def _effect_target_for_component(
    *,
    component: dict,
    ability: AbilityDefinition,
    pending: dict,
    player: Player,
    target_mob: Mob,
    actor: Player | Mob | None = None,
    default_target: Player | Mob | None = None,
) -> tuple[str, int, Player | Mob]:
    actor = actor or player
    default_target = default_target or target_mob
    selector = str(component.get("target") or "ability.target").strip().lower()
    if selector in {"actor", "self", "effect.source"}:
        return _combat_actor_type(actor), actor.id, actor

    pending_target = pending.get("target") or {}
    pending_target_type = str(pending_target.get("type") or "").strip().lower()
    if selector in {"target", "ability.target", "effect.target"}:
        if pending_target_type == "player":
            return "player", player.id, player
        if pending_target_type == "mob":
            return "mob", target_mob.id, target_mob

    ability_target_type = str((ability.target or {}).get("type") or "").strip().lower()
    if ability_target_type in {"self", "ally"}:
        return _combat_actor_type(actor), actor.id, actor
    return _combat_actor_type(default_target), default_target.id, default_target


def _advance_non_ticking_effect_durations(encounter: CombatEncounter) -> None:
    current_round = int(encounter.round_number or 0)
    for effect in encounter_effects(encounter):
        if effect.tick or effect.effect == "stun":
            continue
        if effect.started_round == current_round:
            continue
        remaining = effect.remaining_rounds - 1
        if remaining > 0:
            effect.remaining_rounds = remaining
            effect.rounds_elapsed += 1
            effect.save(update_fields=["remaining_rounds", "rounds_elapsed"])
        else:
            effect.delete()


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
        ], AbilityRoundResult(
            consumed_primary=_ability_consumes_primary_action_while_casting(ability)
        )

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
        if component_targets_character_effect(component, ability=ability):
            target_selector = str(component.get("target") or "").strip().lower()
            if target_selector in {"room.allies", "room.players"}:
                events.extend(
                    execute_character_effect_component(
                        component=component,
                        player=player,
                        ability=ability,
                        room=room,
                        hit_landed=hit_landed,
                        round_id=round_id,
                        encounter=encounter,
                    )
                )
            else:
                _target_type, _target_id, effect_target = _effect_target_for_component(
                    component=component,
                    ability=ability,
                    pending=pending,
                    player=player,
                    target_mob=target_mob,
                )
                scoped_component = {
                    **component,
                    "primitives": _initialize_effect_primitives(
                        component.get("primitives") or [],
                        target=effect_target,
                        source=player,
                    ),
                }
                events.extend(
                    _apply_character_scoped_effect(
                        encounter=encounter,
                        component=scoped_component,
                        source=player,
                        target=effect_target,
                        viewer=player,
                        room=room,
                        ability=ability,
                        round_id=round_id,
                    )
                )
            continue
        effect_type = component.get("effect")
        duration = int(((component.get("duration") or {}).get("rounds")) or 1)
        target_type, target_id, effect_target = _effect_target_for_component(
            component=component,
            ability=ability,
            pending=pending,
            player=player,
            target_mob=target_mob,
        )
        _append_effect(
            encounter,
            effect=effect_type,
            source=player,
            target=effect_target,
            duration_rounds=duration,
            label=_component_label(component, ability),
            category=component.get("category") or "neutral",
            primitives=_initialize_effect_primitives(
                component.get("primitives") or [],
                target=effect_target,
                source=player,
            ),
            tick=component.get("tick") or {},
        )
        events.extend(
            _combat_effect_application_events(
                viewer=player,
                room=room,
                actor=player,
                target=effect_target,
                ability=ability,
                effect=effect_type,
                label=_component_label(component, ability),
                duration_rounds=duration,
                round_id=round_id,
            )
        )

    cooldown_started = start_ability_cooldown(
        player,
        ability,
        hit_landed=hit_landed,
    )
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
        consumed_primary=_ability_consumes_primary_action_on_resolve(ability),
        cooldown_exclude=ability.slug if cooldown_started else None,
    )


def _execute_pending_mob_ability(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    round_id: str,
    player_health_max: int,
) -> tuple[list[GameEvent], AbilityRoundResult]:
    pending = encounter.pending_mob_ability or {}
    if not pending:
        return [], AbilityRoundResult(consumed_primary=False)

    encounter.pending_mob_ability = {}
    ability_slug = str(pending.get("ability") or "").strip().lower()
    ability = _ability_definition_for_mob(target_mob, ability_slug)
    if not ability:
        return [], AbilityRoundResult(consumed_primary=False)

    if _mob_ability_cooldown_remaining(target_mob, ability) > 0:
        return [], AbilityRoundResult(consumed_primary=False)

    target = pending.get("target") or {}
    pending_target_type = str(target.get("type") or "").strip().lower()
    pending_target_id = int(target.get("id") or 0)
    if pending_target_type == "player" and pending_target_id != player.id:
        return [], AbilityRoundResult(consumed_primary=False)
    if pending_target_type == "mob" and pending_target_id != target_mob.id:
        return [], AbilityRoundResult(consumed_primary=False)

    cast_rounds_remaining = _pending_cast_rounds_remaining(pending, ability)
    if cast_rounds_remaining > 0:
        next_remaining = cast_rounds_remaining - 1
        encounter.pending_mob_ability = {
            **pending,
            "status": "casting",
            "cast_rounds_remaining": next_remaining,
        }
        return [
            _mob_ability_casting_event(
                player=player,
                mob=target_mob,
                ability=ability,
                round_id=round_id,
                rounds_remaining=next_remaining,
            )
        ], AbilityRoundResult(
            consumed_primary=_ability_consumes_primary_action_while_casting(ability)
        )

    paid_resource = _pay_mob_ability_cost(target_mob, ability)
    if paid_resource is None and not _mob_can_pay_ability_cost(target_mob, ability):
        return [], AbilityRoundResult(consumed_primary=False)

    events: list[GameEvent] = []
    hit_landed = False
    health_changed = False
    for component in ability.components or []:
        component_type = component.get("type")
        if component_type in {"damage", "healing"}:
            component_target = target_mob if component_type == "healing" else player
            component_events, component_hit = _execute_output_component(
                encounter=encounter,
                player=player,
                target_mob=target_mob,
                room=room,
                component=component,
                ability=ability,
                round_id=round_id,
                player_health_max=player_health_max,
                actor=target_mob,
                target=component_target,
            )
            events.extend(component_events)
            hit_landed = hit_landed or component_hit
            health_changed = health_changed or (
                component_type == "healing" and component_target.pk == target_mob.pk
            )
            if player.health <= 0:
                break
            continue

        if component_type == "state":
            state_event = execute_state_component(
                component=component,
                player=target_mob,
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
        if component_targets_character_effect(component, ability=ability):
            _target_type, _target_id, effect_target = _effect_target_for_component(
                component=component,
                ability=ability,
                pending=pending,
                player=player,
                target_mob=target_mob,
                actor=target_mob,
                default_target=player,
            )
            scoped_component = {
                **component,
                "primitives": _initialize_effect_primitives(
                    component.get("primitives") or [],
                    target=effect_target,
                    source=target_mob,
                ),
            }
            events.extend(
                _apply_character_scoped_effect(
                    encounter=encounter,
                    component=scoped_component,
                    source=target_mob,
                    target=effect_target,
                    viewer=player,
                    room=room,
                    ability=ability,
                    round_id=round_id,
                )
            )
            continue
        effect_type = component.get("effect")
        duration = int(((component.get("duration") or {}).get("rounds")) or 1)
        target_type, target_id, effect_target = _effect_target_for_component(
            component=component,
            ability=ability,
            pending=pending,
            player=player,
            target_mob=target_mob,
            actor=target_mob,
            default_target=player,
        )
        _append_effect(
            encounter,
            effect=effect_type,
            source=target_mob,
            target=effect_target,
            duration_rounds=duration,
            label=_component_label(component, ability),
            category=component.get("category") or "neutral",
            primitives=_initialize_effect_primitives(
                component.get("primitives") or [],
                target=effect_target,
                source=target_mob,
            ),
            tick=component.get("tick") or {},
        )
        events.extend(
            _combat_effect_application_events(
                viewer=player,
                room=room,
                actor=target_mob,
                target=effect_target,
                ability=ability,
                effect=effect_type,
                label=_component_label(component, ability),
                duration_rounds=duration,
                round_id=round_id,
            )
        )

    cooldown_started = _start_mob_ability_cooldown(
        target_mob,
        ability,
        hit_landed=hit_landed,
    )
    update_fields: list[str] = []
    if paid_resource:
        update_fields.append(paid_resource)
    if health_changed:
        update_fields.append("health")
    if cooldown_started:
        update_fields.append("ability_cooldowns")
    if update_fields:
        target_mob.save(update_fields=list(dict.fromkeys(update_fields)))

    return events, AbilityRoundResult(
        consumed_primary=_ability_consumes_primary_action_on_resolve(ability),
        cooldown_exclude=ability.slug if cooldown_started else None,
    )


def _finalize_active_round(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    cooldown_exclude: str | None,
    mob_cooldown_exclude: str | None,
    advance_player_state: bool = True,
    round_id: str | None = None,
) -> bool:
    cooldowns_changed = False
    effects_changed = False
    if advance_player_state:
        cooldowns_changed = decrement_ability_cooldowns(
            player,
            exclude={cooldown_exclude} if cooldown_exclude else set(),
        )
        effects_changed = advance_character_effect_durations(
            player,
            current_round_id=round_id,
            encounter=encounter,
        )
    mob_effects_changed = advance_character_effect_durations(
        target_mob,
        current_round_id=round_id,
        encounter=encounter,
    )
    mob_cooldowns_changed = _decrement_mob_ability_cooldowns(
        target_mob,
        exclude={mob_cooldown_exclude} if mob_cooldown_exclude else set(),
    )
    update_fields: list[str] = []
    if cooldowns_changed:
        update_fields.append("ability_cooldowns")
    if update_fields:
        player.save(update_fields=update_fields)
    if mob_cooldowns_changed:
        target_mob.save(update_fields=["ability_cooldowns"])
    if not encounter._state.adding:
        encounter.save(update_fields=[
            "pending_player_ability",
            "pending_mob_ability",
            "pending_flee",
        ])
    return (
        cooldowns_changed
        or mob_cooldowns_changed
        or effects_changed
        or mob_effects_changed
    )


def _apply_player_primary_turn(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    round_id: str,
    player_health_max: int,
    allow_basic_attack: bool,
    skip_turn: bool,
) -> PlayerTurnOutcome:
    events: list[GameEvent] = []
    if skip_turn:
        return PlayerTurnOutcome(events=events)

    has_pending_ability = bool(encounter.pending_player_ability)
    if not allow_basic_attack and not has_pending_ability:
        return PlayerTurnOutcome(events=events)

    player_stunned = _consume_stun(
        encounter,
        target_type="player",
        target_id=player.id,
    )
    if player_stunned:
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
        return PlayerTurnOutcome(events=events)

    ability_events, ability_result = _execute_pending_player_ability(
        encounter=encounter,
        player=player,
        target_mob=target_mob,
        room=room,
        round_id=round_id,
        player_health_max=player_health_max,
    )
    events.extend(ability_events)
    cooldown_exclude = ability_result.cooldown_exclude

    if target_mob.health <= 0:
        return PlayerTurnOutcome(
            events=events,
            cooldown_exclude=cooldown_exclude,
            target_defeated=True,
        )

    if ability_result.consumed_primary:
        return PlayerTurnOutcome(events=events, cooldown_exclude=cooldown_exclude)

    if not allow_basic_attack:
        return PlayerTurnOutcome(events=events, cooldown_exclude=cooldown_exclude)

    for strike in resolve_attack_routine(actor=player, target=target_mob, world=player.world):
        strike_target = _combat_strike_target(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            actor=player,
            strike=strike,
            default_target=target_mob,
        )
        if strike_target is None:
            continue
        is_primary_target = (
            isinstance(strike_target, Mob)
            and strike_target.pk == target_mob.pk
        )
        strike_outcome = _apply_combat_strike(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            actor=player,
            target=strike_target,
            strike=strike,
            round_id=round_id,
        )
        events.extend(strike_outcome.events)
        if is_primary_target and strike_outcome.target_defeated:
            break
        if (
            not is_primary_target
            and isinstance(strike_target, Mob)
            and strike_outcome.target_defeated
        ):
            _append_mob_defeat_events(
                player=player,
                target_mob=strike_target,
                room=room,
                events=events,
            )

    return PlayerTurnOutcome(
        events=events,
        cooldown_exclude=cooldown_exclude,
        target_defeated=target_mob.health <= 0,
    )


def _apply_mob_primary_turn(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    room: Room,
    round_id: str,
    config,
) -> MobTurnOutcome:
    events: list[GameEvent] = []
    if not target_mob.fights_back:
        return MobTurnOutcome(events=events)

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
        encounter.pending_mob_ability = {}
        return MobTurnOutcome(events=events)

    if not encounter.pending_mob_ability:
        ability = _choose_mob_ability(
            mob=target_mob,
            player=player,
            room=room,
        )
        if ability:
            target_type, target_id = _mob_ability_target_ref(
                ability=ability,
                mob=target_mob,
                player=player,
            )
            encounter.pending_mob_ability = _pending_ability_payload(
                ability=ability,
                command=ability.slug,
                target_type=target_type,
                target_id=target_id,
                queued_round=encounter.round_number,
            )

    ability_events, ability_result = _execute_pending_mob_ability(
        encounter=encounter,
        player=player,
        target_mob=target_mob,
        room=room,
        round_id=round_id,
        player_health_max=player.health_max,
    )
    events.extend(ability_events)
    cooldown_exclude = ability_result.cooldown_exclude

    mob_name = target_mob.name or "Something"
    if player.health <= 0:
        updated_player, death_events = apply_player_death(
            player=player,
            origin_room=room,
            killer=target_mob,
            target_text="You have been slain.",
            room_text=f"{mob_name} kills {player.name}.",
            config=config,
        )
        events.extend(death_events)
        return MobTurnOutcome(
            events=events,
            player_defeated=True,
            actor_key=updated_player.key,
            cooldown_exclude=cooldown_exclude,
        )

    if ability_result.consumed_primary:
        return MobTurnOutcome(events=events, cooldown_exclude=cooldown_exclude)

    for strike in resolve_attack_routine(actor=target_mob, target=player, world=player.world):
        strike_target = _combat_strike_target(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            actor=target_mob,
            strike=strike,
            default_target=player,
        )
        if strike_target is None:
            continue
        strike_outcome = _apply_combat_strike(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            room=room,
            actor=target_mob,
            target=strike_target,
            strike=strike,
            round_id=round_id,
        )
        events.extend(strike_outcome.events)
        if strike_outcome.target_defeated:
            break

    if player.health <= 0:
        updated_player, death_events = apply_player_death(
            player=player,
            origin_room=room,
            killer=target_mob,
            target_text="You have been slain.",
            room_text=f"{mob_name} kills {player.name}.",
            config=config,
        )
        events.extend(death_events)
        return MobTurnOutcome(
            events=events,
            player_defeated=True,
            actor_key=updated_player.key,
            cooldown_exclude=cooldown_exclude,
        )

    return MobTurnOutcome(events=events, cooldown_exclude=cooldown_exclude)


def _apply_encounter_round(
    *,
    encounter: CombatEncounter,
    player: Player,
    target_mob: Mob,
    config,
    player_primary_enabled: bool = True,
    locked_source_player_ids: set[int] | None = None,
) -> CombatStepResult:
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
    mob_cooldown_exclude: str | None = None
    flee_completion_consumed_primary = False

    if (encounter.pending_flee or {}).get("status") == "ready":
        flee_outcome = _complete_flee(
            encounter=encounter,
            player=player,
            round_id=round_id,
        )
        if flee_outcome.terminal_result is not None:
            return flee_outcome.terminal_result
        events.extend(flee_outcome.events)
        flee_completion_consumed_primary = flee_outcome.player_primary_consumed

    effect_outcome = _advance_character_periodic_effects(
        target_player=player,
        target_mob=target_mob,
        encounter=encounter,
        viewer=player,
        round_id=round_id,
        advance_player_character_effects=player_primary_enabled,
        locked_source_player_ids=locked_source_player_ids,
    )
    events.extend(effect_outcome.events)

    if isinstance(effect_outcome.defeated_target, Player):
        killer = effect_outcome.killer
        killer_name = _combat_name(killer) if killer is not None else "An effect"
        updated_player, death_events = apply_player_death(
            player=player,
            origin_room=player.room,
            killer=killer,
            target_text="You succumb to your wounds.",
            room_text=f"{killer_name} kills {player.name}.",
            config=config,
        )
        events.extend(death_events)
        return CombatStepResult(
            actor_key=updated_player.key,
            events=events,
            encounter_active=False,
        )

    if target_mob.health <= 0:
        if isinstance(effect_outcome.killer, Player):
            return _handle_mob_defeated(
                encounter=encounter,
                player=effect_outcome.killer,
                target_mob=target_mob,
                room=room,
                events=events,
            )
        _append_uncredited_mob_defeat_events(
            target_mob=target_mob,
            room=room,
            killer=effect_outcome.killer,
            events=events,
        )
        return CombatStepResult(
            actor_key=player.key,
            events=events,
            encounter_active=False,
        )

    flee_preparation_events = _advance_flee_preparation(
        encounter=encounter,
        player=player,
        round_id=round_id,
    )
    events.extend(flee_preparation_events)
    player_had_pending_ability = bool(encounter.pending_player_ability)

    for actor_ref in _primary_turn_order(
        encounter,
        player=player,
        target_mob=target_mob,
    ):
        if _actor_ref_matches(actor_ref, actor_type="player", actor_id=player.id):
            player_turn = _apply_player_primary_turn(
                encounter=encounter,
                player=player,
                target_mob=target_mob,
                room=room,
                round_id=round_id,
                player_health_max=stats.player_health_max,
                allow_basic_attack=player_primary_enabled,
                skip_turn=(
                    bool(flee_preparation_events)
                    or flee_completion_consumed_primary
                ),
            )
            events.extend(player_turn.events)
            cooldown_exclude = player_turn.cooldown_exclude or cooldown_exclude
            if player_turn.target_defeated:
                return _handle_mob_defeated(
                    encounter=encounter,
                    player=player,
                    target_mob=target_mob,
                    room=room,
                    events=events,
                )
            continue

        if _actor_ref_matches(actor_ref, actor_type="mob", actor_id=target_mob.id):
            mob_turn = _apply_mob_primary_turn(
                encounter=encounter,
                player=player,
                target_mob=target_mob,
                room=room,
                round_id=round_id,
                config=config,
            )
            events.extend(mob_turn.events)
            mob_cooldown_exclude = mob_turn.cooldown_exclude or mob_cooldown_exclude
            if mob_turn.player_defeated:
                return CombatStepResult(
                    actor_key=mob_turn.actor_key,
                    events=events,
                    encounter_active=False,
                )
            continue

        # Future multi-participant encounters should resolve additional actor
        # refs here after CombatParticipant runtime state lands.

    _advance_non_ticking_effect_durations(encounter)
    cooldowns_changed = _finalize_active_round(
        encounter=encounter,
        player=player,
        target_mob=target_mob,
        cooldown_exclude=cooldown_exclude,
        mob_cooldown_exclude=mob_cooldown_exclude,
        advance_player_state=player_primary_enabled or player_had_pending_ability,
        round_id=round_id,
    )
    if cooldown_exclude or cooldowns_changed or effect_outcome.effects_changed:
        events.append(_character_effect_state_event(player))
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

        previous_prepared_slug = _prepared_player_ability_slug(encounter)
        now = timezone.now()
        if auto_advance and encounter.next_resolution_ts and encounter.next_resolution_ts > now:
            return CombatStepResult(
                actor_key=encounter.player.key,
                events=[],
                encounter_active=True,
            )

        source_player_ids = set(
            ActiveEffect.objects.filter(
                scope=ActiveEffect.SCOPE_CHARACTER,
                remaining_rounds__gt=0,
                source_player_id__isnull=False,
            )
            .filter(
                Q(target_player_id=encounter.player_id)
                | Q(target_mob_id=encounter.mob_id)
            )
            .values_list("source_player_id", flat=True)
        )
        source_player_ids.add(encounter.player_id)
        locked_players = {
            actor.id: actor
            for actor in Player.objects.select_for_update()
            .filter(id__in=sorted(source_player_ids))
            .order_by("id")
        }
        player = locked_players[encounter.player_id]
        target_mob = (
            Mob.objects.select_for_update(of=("self",))
            .select_related("definition")
            .filter(pk=encounter.mob_id, is_pending_deletion=False)
            .first()
        )
        if not target_mob:
            _finish_encounter(encounter)
            return _with_ability_prepare_transition(
                CombatStepResult(
                    actor_key=player.key,
                    events=[_combat_effect_state_event(player)],
                    encounter_active=False,
                ),
                player=player,
                previous_slug=previous_prepared_slug,
                current_slug=None,
            )

        if player.room_id != encounter.room_id or target_mob.room_id != encounter.room_id:
            _finish_encounter(encounter)
            return _with_ability_prepare_transition(
                CombatStepResult(
                    actor_key=player.key,
                    events=[_combat_effect_state_event(player)],
                    encounter_active=False,
                ),
                player=player,
                previous_slug=previous_prepared_slug,
                current_slug=None,
            )

        config = player.world.effective_config
        result = _apply_encounter_round(
            encounter=encounter,
            player=player,
            target_mob=target_mob,
            config=config,
            player_primary_enabled=_is_primary_faceoff_encounter(
                encounter=encounter,
                player=player,
                room=encounter.room,
            ),
            locked_source_player_ids=set(locked_players),
        )

        if result.encounter_active and auto_advance and encounter.resolution_interval > 0:
            encounter.next_resolution_ts = timezone.now() + timedelta(
                seconds=encounter.resolution_interval
            )
            encounter.save(update_fields=["next_resolution_ts"])
            next_delay = encounter.resolution_interval
        elif not result.encounter_active:
            _finish_encounter(encounter)

        result = replace(
            result,
            events=[*result.events, _combat_effect_state_event(player)],
        )
        result = _with_ability_prepare_transition(
            result,
            player=player,
            previous_slug=previous_prepared_slug,
            current_slug=_prepared_player_ability_slug(encounter),
        )

    if result.tracker_chase:
        from spawns.actions.mob_movement import ResolveTrackerChaseAction

        try:
            tracker_result = ResolveTrackerChaseAction().execute(
                **result.tracker_chase
            )
        except Exception:
            logger.exception(
                "Failed to resolve tracker chase %s.",
                result.tracker_chase.get("chase_key"),
            )
        else:
            result = replace(
                result,
                events=[*result.events, *tracker_result.events],
                tracker_chase=None,
            )

    if next_delay:
        _schedule_encounter_resolution(encounter_id, next_delay)

    return result


class ScanRoomAggroAction:
    @staticmethod
    def _can_aggro(mob: Mob) -> bool:
        return (
            not getattr(mob, "is_pending_deletion", False)
            and getattr(mob, "attackable", True)
            and int(getattr(mob, "health", 0) or 0) > 0
        )

    def _start_aggro_encounter(
        self,
        *,
        player: Player,
        room: Room,
        mob: Mob,
        primary_mob: Mob | None,
        rules_config,
        death_config,
        player_char_payload: dict | None = None,
        mob_char_payload: dict | None = None,
        primary_mob_char_payload: dict | None = None,
        room_payload: dict | None = None,
    ) -> ActionResult:
        interval = _combat_interval(rules_config)
        events = _aggro_engage_events(
            player=player,
            room=room,
            mob=mob,
            primary_mob=primary_mob,
            player_char_payload=player_char_payload,
            mob_char_payload=mob_char_payload,
            primary_mob_char_payload=primary_mob_char_payload,
            room_payload=room_payload,
        )

        if interval == 0:
            stand_player(player)
            result = KillAction()._resolve_immediately(
                player=player,
                target_mob=mob,
                config=death_config,
            )
            return ActionResult(events=[*events, *result.events])

        stand_player(player)
        encounter = CombatEncounter.objects.create(
            world=player.world,
            room=room,
            player=player,
            mob=mob,
            resolution_interval=interval,
            next_resolution_ts=(
                timezone.now() + timedelta(seconds=interval)
                if interval > 0
                else None
            ),
        )
        ensure_encounter_initiative_order(encounter, player=player, target_mob=mob)

        if interval == -1:
            step = resolve_combat_encounter_step(
                encounter.id,
                auto_advance=False,
            )
            return ActionResult(events=[*events, *step.events])

        _schedule_encounter_resolution(encounter.id, interval)
        return ActionResult(events=events)

    def execute(self, player_id: int, mob_ids: Iterable[int] | None = None) -> ActionResult:
        limited_mob_ids = set(mob_ids) if mob_ids is not None else None
        if limited_mob_ids is not None and not limited_mob_ids:
            return ActionResult()

        with transaction.atomic():
            player = (
                Player.objects.select_for_update()
                .select_related("world")
                .prefetch_related("faction_assignments__faction")
                .get(pk=player_id)
            )
            if (
                player.is_invisible
                or not player.room_id
                or int(player.health or 0) <= 0
            ):
                return ActionResult()

            rules_config = inherited_system_config(player.world)
            if rules_config and not rules_config.allow_combat:
                return ActionResult()
            death_config = player.world.effective_config

            room = Room.objects.select_related("world", "zone").get(pk=player.room_id)
            active_mob_ids = set(
                CombatEncounter.objects.select_for_update()
                .filter(
                    world=player.world,
                    room=room,
                    status=CombatEncounter.STATUS_ACTIVE,
                    mob_id__isnull=False,
                )
                .values_list("mob_id", flat=True)
            )
            mobs = (
                Mob.objects.select_for_update()
                .prefetch_related(
                    "faction_assignments__faction",
                )
                .filter(world=player.world, room=room, is_pending_deletion=False)
                .order_by("id")
            )
            if limited_mob_ids is not None:
                mobs = mobs.filter(id__in=limited_mob_ids)
            aggro_mobs: list[Mob] = []
            for mob in mobs:
                if mob.id in active_mob_ids or not self._can_aggro(mob):
                    continue
                if mob_should_aggro_player(mob, player):
                    aggro_mobs.append(mob)

            if not aggro_mobs:
                return ActionResult()

            aggro_mobs = sorted(aggro_mobs, key=_target_priority_sort_key)
            existing_primary = primary_active_encounter_for_player(player, room=room)
            primary_mob = (
                existing_primary.mob
                if existing_primary and existing_primary.mob
                else aggro_mobs[0]
            )
            events: list[GameEvent] = []
            for mob in aggro_mobs:
                result = self._start_aggro_encounter(
                    player=player,
                    room=room,
                    mob=mob,
                    primary_mob=primary_mob,
                    rules_config=rules_config,
                    death_config=death_config,
                )
                events.extend(result.events)
                active_mob_ids.add(mob.id)
                player.refresh_from_db(fields=["health", "room"])
                if int(player.health or 0) <= 0 or player.room_id != room.id:
                    break
            return ActionResult(events=events)

        return ActionResult()


class FleeAction:
    def execute(self, player_id: int) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            room = Room.objects.filter(pk=player.room_id).first() if player.room_id else None
            encounter = primary_active_encounter_for_player(
                player,
                room=room,
                lock=True,
            )
            if not encounter:
                raise ActionError("You are not in combat.", code="not_in_combat")

            if player.room_id != encounter.room_id:
                _finish_encounter(encounter)
                message = "You are no longer in that fight."
                return ActionResult(events=[
                    GameEvent(
                        type="cmd.flee.error",
                        recipients=[player.key],
                        data={"error": message, "code": "combat_ended"},
                        text=message,
                    ),
                    ability_prepare_state_event(player),
                ])

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

            prevention = preventing_action_effect(
                player,
                "flee",
                phase="before_action",
            )
            if prevention:
                raise ActionError(
                    _action_prevention_message(prevention, action="flee"),
                    code="action_prevented",
                    data=_action_prevention_data(prevention, action="flee"),
                )

            destination = _choose_flee_destination(player)
            player.stamina = max(0, int(player.stamina or 0) - destination.movement_cost)
            player.save(update_fields=["stamina"])
            prepared_ability_slug = _prepared_player_ability_slug(encounter)
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
            if prepared_ability_slug:
                events.append(ability_prepare_state_event(player))

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

        encounter = CombatEncounter.objects.create(
            world=player.world,
            room=room,
            player=player,
            mob=target_mob,
            resolution_interval=0,
        )
        ensure_encounter_initiative_order(
            encounter,
            player=player,
            target_mob=target_mob,
        )
        for round_no in range(1, MAX_AUTO_RESOLVE_ROUNDS + 1):
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

            rules_config = inherited_system_config(player.world)
            if rules_config and not rules_config.allow_combat:
                raise ActionError("Combat is disabled here.", code="combat_disabled")
            death_config = player.world.effective_config

            room = Room.objects.select_related("world", "zone").get(pk=player.room_id)
            active_player_encounter = primary_active_encounter_for_player(
                player,
                room=room,
                lock=True,
            )
            if active_player_encounter and not str(target_selector or "").strip():
                target_ref = active_player_encounter.mob
            else:
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
                .filter(
                    pk=target_ref.id,
                    world=player.world,
                    room=room,
                    is_pending_deletion=False,
                )
                .first()
            )
            if not target_mob:
                raise ActionError("You don't see them here.", code="target_missing")
            if not getattr(target_mob, "attackable", True):
                raise ActionError("You cannot attack them.", code="not_attackable")

            interval = _combat_interval(rules_config)

            if active_player_encounter:
                active_name = _encounter_mob_name(active_player_encounter)
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
                    config=death_config,
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
            ensure_encounter_initiative_order(encounter, player=player, target_mob=target_mob)

            events = _engage_events(player=player, room=room, mob=target_mob)

            if interval == -1:
                step = resolve_combat_encounter_step(
                    encounter.id,
                    auto_advance=False,
                )
                return ActionResult(events=[*events, *step.events])

            _schedule_encounter_resolution(encounter.id, interval)
            return ActionResult(events=events)
