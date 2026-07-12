from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any

from django.db.models import Q, QuerySet
from django.utils import timezone

from config import constants as adv_consts
from config import game_settings as adv_config
from core.world_config import inherited_system_config
from spawns.models import ActiveEffect, CombatEncounter, Mob, Player


ROOM_ALLY_TARGETS = {"room.allies", "room.players"}
CHARACTER_SELF_TARGETS = {"actor", "self", "effect.source"}


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value or default))
    except (TypeError, ValueError):
        return default


def actor_effect_ref(actor: Any) -> dict[str, Any]:
    actor_type = "player" if isinstance(actor, Player) else "mob"
    return {"type": actor_type, "id": int(getattr(actor, "id", 0) or 0)}


def _actor_effect_queryset(actor: Player | Mob) -> QuerySet[ActiveEffect]:
    if isinstance(actor, Player):
        return ActiveEffect.objects.filter(target_player_id=actor.id)
    return ActiveEffect.objects.filter(target_mob_id=actor.id)


def active_effect_payload(effect: ActiveEffect) -> dict[str, Any]:
    source = (
        actor_effect_ref(effect.source_player)
        if effect.source_player_id
        else actor_effect_ref(effect.source_mob)
        if effect.source_mob_id
        else deepcopy(effect.source_snapshot.get("ref") or {})
    )
    target = (
        actor_effect_ref(effect.target_player)
        if effect.target_player_id
        else actor_effect_ref(effect.target_mob)
    )
    payload: dict[str, Any] = {
        "id": effect.id,
        "effect": effect.effect,
        "category": effect.category,
        "scope": effect.scope,
        "source": source,
        "target": target,
        "remaining_rounds": effect.remaining_rounds,
        "duration_rounds": effect.duration_rounds,
        "rounds_elapsed": effect.rounds_elapsed,
        "label": effect.label,
        "primitives": deepcopy(effect.primitives or []),
    }
    if effect.stack_key:
        payload["stack_key"] = effect.stack_key
    if effect.stacking:
        payload["stacking"] = effect.stacking
    if effect.started_round:
        payload["started_round"] = effect.started_round
    if effect.started_round_id:
        payload["started_round_id"] = effect.started_round_id
    if effect.tick:
        payload["tick"] = deepcopy(effect.tick)
    return payload


def clear_actor_effect_cache(actor: Player | Mob | None) -> None:
    return None


def active_character_effects(actor: Player | Mob) -> list[dict[str, Any]]:
    if not getattr(actor, "id", None):
        return []
    effects = list(
        _actor_effect_queryset(actor)
        .filter(scope=ActiveEffect.SCOPE_CHARACTER, remaining_rounds__gt=0)
        .select_related("source_player", "source_mob", "target_player", "target_mob")
        .order_by("created_ts", "id")
    )
    payloads = [active_effect_payload(effect) for effect in effects]
    return deepcopy(payloads)


def active_combat_effects(player: Player) -> list[dict[str, Any]]:
    """Return encounter-scoped effects that currently target ``player``."""
    rows = (
        ActiveEffect.objects.filter(
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            encounter__status=CombatEncounter.STATUS_ACTIVE,
            target_player=player,
            remaining_rounds__gt=0,
        )
        .select_related("source_player", "source_mob", "target_player", "target_mob")
        .order_by("created_ts", "id")
    )
    return [
        {**active_effect_payload(effect), "encounter_id": effect.encounter_id}
        for effect in rows
    ]


def encounter_effects(encounter: CombatEncounter) -> list[ActiveEffect]:
    return list(
        ActiveEffect.objects.filter(
            encounter=encounter,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            remaining_rounds__gt=0,
        )
        .select_related("source_player", "source_mob", "target_player", "target_mob")
        .order_by("created_ts", "id")
    )


def _combat_modifier_primitives(component: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        primitive
        for primitive in component.get("primitives") or []
        if isinstance(primitive, dict) and primitive.get("type") == "combat_modifier"
    ]


def _stat_modifier_primitives(component: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        primitive
        for primitive in component.get("primitives") or []
        if isinstance(primitive, dict) and primitive.get("type") == "stat_modifier"
    ]


def component_targets_character_effect(
    component: dict[str, Any],
    *,
    ability: Any | None = None,
) -> bool:
    explicit_scope = str(component.get("scope") or "").strip().lower()
    if explicit_scope:
        return explicit_scope == ActiveEffect.SCOPE_CHARACTER
    if component.get("tick") or component.get("effect") in {"dot", "hot"}:
        return True
    target_selector = str(component.get("target") or "").strip().lower()
    if target_selector in ROOM_ALLY_TARGETS:
        return True
    if _combat_modifier_primitives(component):
        return True
    if not _stat_modifier_primitives(component):
        return False
    if target_selector in CHARACTER_SELF_TARGETS:
        return True
    if target_selector == "ability.target":
        ability_target = str(
            (getattr(ability, "target", None) or {}).get("type") or ""
        ).strip().lower()
        return ability_target == "self"
    return False


def room_ally_players(actor: Player, *, room=None) -> list[Player]:
    current_room = room or getattr(actor, "room", None)
    if current_room is None:
        return [actor]
    return list(
        Player.objects.select_for_update()
        .filter(world=actor.world, room=current_room)
        .filter(Q(in_game=True) | Q(pk=actor.pk))
        .order_by("id")
    )


def targets_for_character_effect_component(
    *,
    actor: Player,
    component: dict[str, Any],
    ability: Any | None = None,
    room=None,
) -> list[Player]:
    target_selector = str(component.get("target") or "self").strip().lower()
    if target_selector in ROOM_ALLY_TARGETS:
        targets: list[Player] = []
        seen: set[int] = set()
        for target in room_ally_players(actor, room=room):
            if target.id in seen:
                continue
            seen.add(target.id)
            targets.append(target)
        return targets
    if target_selector == "ability.target":
        ability_target = str(
            (getattr(ability, "target", None) or {}).get("type") or ""
        ).strip().lower()
        if ability_target == "self":
            return [actor]
    return [actor]


def _source_snapshot(source: Player | Mob) -> dict[str, Any]:
    from core.combat_formulas import combatant_snapshot

    snapshot = combatant_snapshot(source, world=getattr(source, "world", None))
    return {
        "ref": actor_effect_ref(source),
        "key": getattr(source, "key", ""),
        "name": str(getattr(source, "name", "") or ""),
        "level": snapshot.level,
        "actor_type": snapshot.actor_type,
        "stats": snapshot.stats,
        "weapon_damage": snapshot.weapon_damage,
        "is_disarmed": snapshot.is_disarmed,
        "outgoing_damage_multiplier": snapshot.outgoing_damage_multiplier,
    }


def build_character_effect(
    *,
    component: dict[str, Any],
    source: Player | Mob,
    target: Player | Mob,
    round_id: str | None = None,
    started_round: int = 0,
) -> dict[str, Any]:
    duration = _positive_int((component.get("duration") or {}).get("rounds"), default=1) or 1
    text = component.get("text") or {}
    effect_key = str(component.get("effect") or "").strip().lower()
    effect = {
        "effect": effect_key,
        "category": str(component.get("category") or "neutral").strip().lower(),
        "scope": ActiveEffect.SCOPE_CHARACTER,
        "source": actor_effect_ref(source),
        "target": actor_effect_ref(target),
        "remaining_rounds": duration,
        "duration_rounds": duration,
        "rounds_elapsed": 0,
        "started_round": max(0, int(started_round or 0)),
        "label": str(text.get("label") or effect_key or "Effect").strip(),
        "primitives": deepcopy(component.get("primitives") or []),
        "tick": deepcopy(component.get("tick") or {}),
        "source_snapshot": _source_snapshot(source),
    }
    stack_key = str(component.get("stack_key") or "").strip().lower()
    if stack_key:
        effect["stack_key"] = stack_key
    stacking = str(component.get("stacking") or "").strip().lower()
    if stacking:
        effect["stacking"] = stacking
    if round_id:
        effect["started_round_id"] = round_id
    return effect


def _effect_is_hostile(effect: dict[str, Any]) -> bool:
    if str(effect.get("effect") or "").strip().lower() == "dot":
        return True
    tick_component = (effect.get("tick") or {}).get("component") or {}
    return (
        str(effect.get("category") or "").strip().lower() == "debuff"
        and tick_component.get("type") == "damage"
    )


def _effect_actor_fields(actor: Player | Mob, *, prefix: str) -> dict[str, Any]:
    if isinstance(actor, Player):
        return {f"{prefix}_player": actor, f"{prefix}_mob": None}
    return {f"{prefix}_player": None, f"{prefix}_mob": actor}


def _detached_interval_seconds(world) -> float:
    config = inherited_system_config(world)
    try:
        combat_interval = float(getattr(config, "combat_resolution_interval", 0) or 0)
    except (TypeError, ValueError):
        combat_interval = 0
    if combat_interval > 0:
        return combat_interval
    try:
        heartbeat_interval = float(getattr(adv_config, "GAME_HEARTBEAT_INTERVAL_SECONDS", 2) or 2)
    except (TypeError, ValueError):
        heartbeat_interval = 2
    return max(1.0, heartbeat_interval)


def refresh_or_add_character_effect(
    target: Player | Mob,
    effect: dict[str, Any],
    *,
    source: Player | Mob,
    encounter: CombatEncounter | None = None,
) -> str:
    stack_key = str(effect.get("stack_key") or "").strip().lower()
    stacking = str(effect.get("stacking") or "independent").strip().lower()
    existing = None
    if stack_key and stacking == "refresh":
        existing = (
            _actor_effect_queryset(target)
            .select_for_update()
            .filter(scope=ActiveEffect.SCOPE_CHARACTER, stack_key=stack_key)
            .order_by("id")
            .first()
        )

    values = {
        "world": target.world,
        "encounter": encounter,
        **_effect_actor_fields(source, prefix="source"),
        **_effect_actor_fields(target, prefix="target"),
        "scope": ActiveEffect.SCOPE_CHARACTER,
        "effect": effect.get("effect") or "effect",
        "category": effect.get("category") or "neutral",
        "label": effect.get("label") or effect.get("effect") or "Effect",
        "stack_key": stack_key,
        "stacking": stacking,
        "remaining_rounds": _positive_int(effect.get("remaining_rounds"), default=1) or 1,
        "duration_rounds": _positive_int(effect.get("duration_rounds"), default=1) or 1,
        "rounds_elapsed": _positive_int(effect.get("rounds_elapsed")),
        "started_round": _positive_int(effect.get("started_round")),
        "started_round_id": str(effect.get("started_round_id") or ""),
        "primitives": deepcopy(effect.get("primitives") or []),
        "tick": deepcopy(effect.get("tick") or {}),
        "source_snapshot": deepcopy(effect.get("source_snapshot") or _source_snapshot(source)),
        "is_hostile": _effect_is_hostile(effect),
        "next_tick_ts": timezone.now() + timedelta(
            seconds=_detached_interval_seconds(target.world)
        ),
        "last_tick_ts": None,
        "last_tick_token": "",
    }
    if existing is None:
        ActiveEffect.objects.create(**values)
        action = "applied"
    else:
        for field_name, value in values.items():
            setattr(existing, field_name, value)
        existing.save(update_fields=list(values.keys()))
        action = "refreshed"
    clear_actor_effect_cache(target)
    return action


def next_character_effect_tick_ts(world):
    return timezone.now() + timedelta(seconds=_detached_interval_seconds(world))


def advance_character_effect_durations(
    actor: Player | Mob,
    *,
    current_round_id: str | None = None,
    encounter: CombatEncounter | None = None,
    due_at=None,
) -> bool:
    queryset = _actor_effect_queryset(actor).filter(
        scope=ActiveEffect.SCOPE_CHARACTER,
        remaining_rounds__gt=0,
        tick={},
    )
    if encounter is None:
        queryset = queryset.filter(next_tick_ts__lte=due_at or timezone.now())
    effects = list(queryset)
    changed = False
    for effect in effects:
        has_attack_routine_modifier = any(
            isinstance(primitive, dict)
            and primitive.get("type") == "combat_modifier"
            and primitive.get("phase") == "attack_routine"
            for primitive in effect.primitives or []
        )
        if (
            current_round_id
            and effect.started_round_id == current_round_id
            and not has_attack_routine_modifier
        ):
            continue
        remaining = effect.remaining_rounds - 1
        if remaining <= 0:
            effect.delete()
        else:
            effect.remaining_rounds = remaining
            effect.rounds_elapsed += 1
            effect.last_tick_token = current_round_id or effect.last_tick_token
            effect.last_tick_ts = timezone.now()
            effect.next_tick_ts = next_character_effect_tick_ts(effect.world)
            effect.save(
                update_fields=[
                    "remaining_rounds",
                    "rounds_elapsed",
                    "last_tick_token",
                    "last_tick_ts",
                    "next_tick_ts",
                ]
            )
        changed = True
    if changed:
        clear_actor_effect_cache(actor)
    return changed


def _live_hostile_effects() -> QuerySet[ActiveEffect]:
    return ActiveEffect.objects.filter(
        is_hostile=True,
        remaining_rounds__gt=0,
        world__lifecycle=adv_consts.WORLD_LIFECYCLE_RUNNING,
    ).filter(
        Q(target_player__in_game=True, target_player__room_id__isnull=False)
        | Q(
            target_mob__is_pending_deletion=False,
            target_mob__health__gt=0,
            target_mob__room_id__isnull=False,
        )
    )


def combat_tagged_actor_ids() -> tuple[set[int], set[int]]:
    effects = _live_hostile_effects()
    player_ids = set(
        effects.filter(source_player_id__isnull=False).values_list("source_player_id", flat=True)
    )
    player_ids.update(
        effects.filter(target_player_id__isnull=False).values_list("target_player_id", flat=True)
    )
    mob_ids = set(
        effects.filter(source_mob_id__isnull=False).values_list("source_mob_id", flat=True)
    )
    mob_ids.update(
        effects.filter(target_mob_id__isnull=False).values_list("target_mob_id", flat=True)
    )
    return player_ids, mob_ids


def actor_is_combat_tagged(actor: Player | Mob) -> bool:
    """Return whether ``actor`` owns either side of a live hostile effect."""
    effects = _live_hostile_effects()
    if isinstance(actor, Player):
        return effects.filter(Q(source_player=actor) | Q(target_player=actor)).exists()
    return effects.filter(Q(source_mob=actor) | Q(target_mob=actor)).exists()
