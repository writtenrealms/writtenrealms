from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.utils import NotSupportedError
from django.utils import timezone

from builders.models import AbilityDefinition, MobDefinition
from core.abilities import definition_world, max_known_abilities_for_world
from core.combat_formulas import resolve_attack
from core.computations import compute_stats
from core.condition_dsl import (
    ConditionContext,
    evaluate_condition,
    is_structured_condition_mapping,
    resolve_path,
)
from core.scoped_state import (
    clear_state_value,
    increment_state_value,
    resolve_scope_owner,
    set_state_value,
)
from spawns.actions.base import ActionError, ActionResult
from spawns.actions.targeting import resolve_room_mob_target
from spawns.events import GameEvent
from spawns.models import CombatEncounter, Mob, Player
from spawns.state_payloads import serialize_char_from_mob, serialize_char_from_player
from worlds.models import Room


MAX_ABILITY_HOTKEY = 8


@dataclass(frozen=True)
class AbilityCostCheck:
    ok: bool
    resource: str
    amount: int
    current: int


def _definition_world_id(world) -> int:
    source_world = definition_world(world)
    return source_world.id


def _ability_queryset_for_world(world):
    return AbilityDefinition.objects.filter(
        world_id=_definition_world_id(world),
        is_active=True,
    )


def resolve_ability_for_command(world, command: str) -> AbilityDefinition | None:
    normalized = str(command or "").strip().lower()
    if not normalized:
        return None
    exact = _ability_queryset_for_world(world).filter(slug=normalized).first()
    if exact:
        return exact

    try:
        ability = _ability_queryset_for_world(world).filter(
            command_verbs__contains=[normalized],
        ).first()
    except NotSupportedError:
        ability = None
    if ability:
        return ability

    # Non-Postgres development databases may not support JSON containment.
    # Keep a fallback so local smoke tests still work.
    for ability in _ability_queryset_for_world(world).only(
        "id",
        "slug",
        "name",
        "command_verbs",
        "world_id",
        "action_type",
        "target",
        "availability",
        "requirements",
        "cost",
        "cooldown",
        "components",
        "is_active",
    ):
        if normalized in [str(verb).strip().lower() for verb in (ability.command_verbs or [])]:
            return ability
    return None


def resolve_ability_for_selector(world, selector: str | None) -> AbilityDefinition | None:
    text = str(selector or "").strip().lower()
    if not text:
        return None

    ability = resolve_ability_for_command(world, text)
    if ability:
        return ability

    candidates = list(_ability_queryset_for_world(world))
    exact_name = [candidate for candidate in candidates if (candidate.name or "").strip().lower() == text]
    if exact_name:
        return exact_name[0]

    slug_matches = [candidate for candidate in candidates if candidate.slug.startswith(text)]
    if len(slug_matches) == 1:
        return slug_matches[0]

    name_matches = [
        candidate
        for candidate in candidates
        if (candidate.name or "").strip().lower().startswith(text)
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    return None


def known_ability_slugs(player: Player) -> list[str]:
    if not isinstance(player.known_abilities, list):
        return []
    known: list[str] = []
    for raw_slug in player.known_abilities:
        slug = str(raw_slug or "").strip().lower()
        if slug and slug not in known:
            known.append(slug)
    return known


def ability_hotkeys(player: Player) -> dict[str, str]:
    if not isinstance(player.ability_hotkeys, dict):
        return {}

    normalized: dict[str, str] = {}
    assigned_slugs: set[str] = set()
    for raw_slot, raw_slug in player.ability_hotkeys.items():
        try:
            slot_number = int(raw_slot)
        except (TypeError, ValueError):
            continue
        if slot_number < 1 or slot_number > MAX_ABILITY_HOTKEY:
            continue

        slug = str(raw_slug or "").strip().lower()
        if not slug or slug in assigned_slugs:
            continue

        normalized[str(slot_number)] = slug
        assigned_slugs.add(slug)
    return normalized


def ability_state_payload(player: Player) -> dict[str, Any]:
    return {
        "key": player.key,
        "known_abilities": known_ability_slugs(player),
        "ability_hotkeys": ability_hotkeys(player),
        "ability_cooldowns": _cooldowns(player),
    }


def ability_state_event(player: Player) -> GameEvent:
    return GameEvent(
        type="player.abilities.update",
        recipients=[player.key],
        data={"actor": ability_state_payload(player)},
    )


def _assign_next_ability_hotkey(player: Player, ability: AbilityDefinition) -> str | None:
    hotkeys = ability_hotkeys(player)
    for slot, slug in hotkeys.items():
        if slug == ability.slug:
            return slot

    for slot_number in range(1, MAX_ABILITY_HOTKEY + 1):
        slot = str(slot_number)
        if slot not in hotkeys:
            hotkeys[slot] = ability.slug
            player.ability_hotkeys = hotkeys
            return slot
    return None


def _remove_ability_hotkey(player: Player, ability: AbilityDefinition) -> bool:
    hotkeys = ability_hotkeys(player)
    updated = {
        slot: slug
        for slot, slug in hotkeys.items()
        if slug != ability.slug
    }
    if updated == hotkeys:
        return False
    player.ability_hotkeys = updated
    return True


def resolve_ability_for_hotkey(player: Player, hotkey: str | int | None) -> AbilityDefinition | None:
    try:
        slot_number = int(hotkey)
    except (TypeError, ValueError):
        return None
    if slot_number < 1 or slot_number > MAX_ABILITY_HOTKEY:
        return None

    slug = ability_hotkeys(player).get(str(slot_number))
    if not slug:
        return None
    if slug not in known_ability_slugs(player):
        return None
    return _ability_queryset_for_world(player.world).filter(slug=slug).first()


def player_knows_ability(player: Player, ability: AbilityDefinition) -> bool:
    return ability.slug in set(known_ability_slugs(player))


def _legacy_ability_requirements_condition(requirements: dict[str, Any]) -> Any:
    equipment = requirements.get("equipment")
    if not isinstance(equipment, dict):
        return {}

    conditions: list[dict[str, Any]] = []
    offhand_type = str(equipment.get("offhand_type") or "").strip()
    if offhand_type:
        conditions.append({
            "eq": ["actor.equipment.offhand.equipment_type", offhand_type],
        })

    weapon_type = str(equipment.get("weapon_type") or "").strip()
    if weapon_type:
        conditions.append({
            "eq": ["actor.equipment.weapon.weapon_type", weapon_type],
        })

    if not conditions:
        return {}
    if len(conditions) == 1:
        return conditions[0]
    return {"all": conditions}


def ability_requirements_condition(ability: AbilityDefinition) -> Any:
    requirements = ability.requirements or {}
    if not isinstance(requirements, dict):
        return {}
    if "conditions" in requirements:
        return requirements.get("conditions") or {}
    if is_structured_condition_mapping(requirements):
        return requirements
    return _legacy_ability_requirements_condition(requirements)


def ability_requirements_met(player: Player, ability: AbilityDefinition) -> bool:
    condition = ability_requirements_condition(ability)
    if condition in (None, {}, []):
        return True
    return evaluate_condition(
        condition,
        context=ConditionContext(
            actor=player,
            player=player,
            room=getattr(player, "room", None),
            zone=getattr(getattr(player, "room", None), "zone", None),
            world=getattr(player, "world", None),
            ability=ability,
        ),
    )


def _ability_condition_context(
    *,
    player: Player,
    ability: AbilityDefinition,
    room: Room | None = None,
) -> ConditionContext:
    current_room = room or getattr(player, "room", None)
    return ConditionContext(
        actor=player,
        player=player,
        room=current_room,
        zone=getattr(current_room, "zone", None),
        world=getattr(player, "world", None),
        ability=ability,
    )


def ability_component_overrides(
    component: dict,
    *,
    player: Player,
    ability: AbilityDefinition,
    room: Room | None = None,
) -> dict[str, Any]:
    overrides = dict(component.get("overrides") or {})
    scaling = component.get("scaling") or {}
    if not isinstance(scaling, dict):
        return overrides

    source = str(scaling.get("from") or "").strip()
    if not source:
        return overrides
    raw_value = resolve_path(
        source,
        _ability_condition_context(player=player, ability=ability, room=room),
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


def _state_owner_for_ability_component(
    *,
    component: dict,
    player: Player,
    room: Room | None,
):
    scope = str(component.get("scope") or "character").strip().lower()
    current_room = room or getattr(player, "room", None)
    return resolve_scope_owner(
        scope,
        actor=player,
        world=getattr(player, "world", None),
        zone=getattr(current_room, "zone", None),
        room=current_room,
        character=player,
    )


def _clamp_state_component_value(value: Any, component: dict) -> Any:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return value
    if "min" in component:
        try:
            numeric_value = max(numeric_value, float(component.get("min")))
        except (TypeError, ValueError):
            pass
    if "max" in component:
        try:
            numeric_value = min(numeric_value, float(component.get("max")))
        except (TypeError, ValueError):
            pass
    if numeric_value.is_integer():
        return int(numeric_value)
    return numeric_value


def execute_state_component(
    *,
    component: dict,
    player: Player,
    ability: AbilityDefinition,
    room: Room | None = None,
    hit_landed: bool = False,
    round_id: str | None = None,
) -> GameEvent | None:
    if component.get("type") != "state":
        return None
    if component.get("apply") == "on_hit" and not hit_landed:
        return None

    scope = str(component.get("scope") or "character").strip().lower()
    key = str(component.get("key") or "").strip()
    owner = _state_owner_for_ability_component(
        component=component,
        player=player,
        room=room,
    )
    if owner is None or not key:
        return None

    operation = str(component.get("op") or "increment").strip().lower()
    data: dict[str, Any] = {
        "ability": ability.slug,
        "scope": scope,
        "key": key,
        "operation": operation,
    }
    if round_id:
        data["round_id"] = round_id

    if operation == "clear":
        data["cleared"] = clear_state_value(scope, owner, key)
        text = f"{ability.name} clears {scope}.{key}."
    elif operation == "set":
        data["value"] = set_state_value(scope, owner, key, component.get("value"))
        text = f"{ability.name} sets {scope}.{key}."
    else:
        value = increment_state_value(scope, owner, key, component.get("amount", 1))
        clamped = _clamp_state_component_value(value, component)
        if clamped != value:
            value = set_state_value(scope, owner, key, clamped)
        data["value"] = value
        text = f"{ability.name} updates {scope}.{key}."

    return GameEvent(
        type="notification.ability.state",
        recipients=[player.key],
        data=data,
        text=text,
    )


def _trainer_config(definition: MobDefinition | None) -> dict[str, Any]:
    if not definition or not isinstance(definition.trainer, dict):
        return {}
    abilities = []
    for raw_slug in definition.trainer.get("abilities") or []:
        slug = str(raw_slug or "").strip().lower()
        if slug and slug not in abilities:
            abilities.append(slug)
    if not abilities:
        return {}
    availability = str(definition.trainer.get("availability") or "present").strip().lower()
    if availability not in {"present", "alive_and_present"}:
        availability = "present"
    return {
        "abilities": abilities,
        "availability": availability,
    }


def _trainer_teaches_ability(definition: MobDefinition | None, ability: AbilityDefinition) -> bool:
    return ability.slug in set(_trainer_config(definition).get("abilities") or [])


def ability_has_trainers(world, ability: AbilityDefinition) -> bool:
    for definition in MobDefinition.objects.filter(
        world_id=_definition_world_id(world),
    ).only("id", "trainer"):
        if _trainer_teaches_ability(definition, ability):
            return True
    return False


def _mob_can_train_ability(mob: Mob, ability: AbilityDefinition) -> bool:
    config = _trainer_config(mob.definition)
    if ability.slug not in set(config.get("abilities") or []):
        return False
    if config.get("availability") == "alive_and_present":
        if mob.is_pending_deletion:
            return False
        try:
            if int(mob.health or 0) <= 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def trainer_for_ability_change(player: Player, ability: AbilityDefinition) -> Mob | None:
    if not player.room_id:
        return None
    for mob in Mob.objects.filter(
        room_id=player.room_id,
        is_pending_deletion=False,
        definition__world_id=_definition_world_id(player.world),
    ).select_related("definition").order_by("id"):
        if _mob_can_train_ability(mob, ability):
            return mob
    return None


def require_trainer_for_ability_change(
    player: Player,
    ability: AbilityDefinition,
    *,
    verb: str,
) -> Mob | None:
    if not ability_has_trainers(player.world, ability):
        return None
    trainer = trainer_for_ability_change(player, ability)
    if trainer:
        return trainer
    raise ActionError(
        f"You need a trainer to {verb} {ability.name}.",
        code="ability_trainer_required",
        data={"ability": ability.slug},
    )


def ability_is_available_to_player(player: Player, ability: AbilityDefinition) -> tuple[bool, str]:
    availability = ability.availability or {}
    min_level = int(availability.get("min_level") or 1)
    if int(player.level or 1) < min_level:
        return False, f"You must be level {min_level} to learn {ability.name}."

    classes = [
        str(item or "").strip().lower()
        for item in availability.get("classes", [])
        if str(item or "").strip()
    ]
    if classes:
        archetype = str(player.archetype or "").strip().lower()
        if archetype not in classes:
            return False, f"{ability.name} is not available to your class."

    if not ability_requirements_met(player, ability):
        return False, f"You do not meet the requirements for {ability.name}."

    return True, ""


def _cooldowns(player: Player) -> dict[str, int]:
    if not isinstance(player.ability_cooldowns, dict):
        return {}
    normalized: dict[str, int] = {}
    for raw_slug, raw_value in player.ability_cooldowns.items():
        slug = str(raw_slug or "").strip().lower()
        if not slug:
            continue
        try:
            value = int(raw_value or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            normalized[slug] = value
    return normalized


def cooldown_remaining(player: Player, ability: AbilityDefinition) -> int:
    return _cooldowns(player).get(ability.slug, 0)


def decrement_ability_cooldowns(player: Player, *, exclude: set[str] | None = None) -> bool:
    exclude = exclude or set()
    cooldowns = _cooldowns(player)
    if not cooldowns:
        if player.ability_cooldowns not in ({}, None):
            player.ability_cooldowns = {}
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
    if updated == player.ability_cooldowns:
        return False
    player.ability_cooldowns = updated
    return True


def start_ability_cooldown(player: Player, ability: AbilityDefinition) -> bool:
    rounds = int((ability.cooldown or {}).get("rounds") or 0)
    if rounds <= 0:
        return False
    cooldowns = _cooldowns(player)
    cooldowns[ability.slug] = rounds
    player.ability_cooldowns = cooldowns
    return True


def _resource_max(player: Player, resource: str) -> int:
    stats = compute_stats(
        player.level,
        player.archetype,
        char=player,
        world=player.world,
    )
    if resource == "health":
        return max(1, int(stats.get("health_max") or getattr(player, "health_max", 1) or 1))
    if resource == "stamina":
        return max(0, int(stats.get("stamina_max") or getattr(player, "stamina_max", 0) or 0))
    return max(0, int(stats.get("energy_max") or getattr(player, "energy_max", 0) or 0))


def _resource_base(player: Player, resource: str) -> int:
    stats = compute_stats(
        player.level,
        player.archetype,
        char=player,
        world=player.world,
    )
    if resource == "health":
        return max(0, int(stats.get("health_base") or 0))
    if resource == "stamina":
        return max(0, int(stats.get("stamina_base") or 0))
    return max(0, int(stats.get("energy_base") or 0))


def ability_cost_amount(player: Player, ability: AbilityDefinition) -> tuple[str, int]:
    cost = ability.cost or {}
    if not cost:
        return "", 0
    resource = str(cost.get("resource") or "").strip().lower()
    amount = float(cost.get("amount") or 0)
    calc = str(cost.get("calc") or "fixed").strip().lower()
    if calc == "percent_max":
        amount = _resource_max(player, resource) * (amount / 100)
    elif calc == "percent_base":
        amount = _resource_base(player, resource) * (amount / 100)
    return resource, max(0, int(amount))


def check_ability_cost(player: Player, ability: AbilityDefinition) -> AbilityCostCheck:
    resource, amount = ability_cost_amount(player, ability)
    if not resource or amount <= 0:
        return AbilityCostCheck(True, resource, 0, 0)
    current = int(getattr(player, resource, 0) or 0)
    return AbilityCostCheck(current >= amount, resource, amount, current)


def pay_ability_cost(player: Player, ability: AbilityDefinition) -> bool:
    check = check_ability_cost(player, ability)
    if not check.resource or check.amount <= 0:
        return False
    if not check.ok:
        raise ActionError(
            f"You need {check.amount} {check.resource} to use {ability.name}.",
            code="insufficient_resource",
            data={"resource": check.resource, "required": check.amount, "current": check.current},
        )
    setattr(player, check.resource, max(0, check.current - check.amount))
    return True


def validate_ability_ready(player: Player, ability: AbilityDefinition) -> None:
    if not player_knows_ability(player, ability):
        raise ActionError(f"You do not know {ability.name}.", code="ability_unknown")

    available, reason = ability_is_available_to_player(player, ability)
    if not available:
        raise ActionError(reason, code="ability_unavailable")

    remaining = cooldown_remaining(player, ability)
    if remaining > 0:
        raise ActionError(
            f"{ability.name} is not ready for {remaining} more round{'s' if remaining != 1 else ''}.",
            code="ability_on_cooldown",
            data={"ability": ability.slug, "rounds_remaining": remaining},
        )

    cost = check_ability_cost(player, ability)
    if not cost.ok:
        raise ActionError(
            f"You need {cost.amount} {cost.resource} to use {ability.name}.",
            code="insufficient_resource",
            data={"resource": cost.resource, "required": cost.amount, "current": cost.current},
        )


def _ability_ack(
    *,
    player: Player,
    ability: AbilityDefinition,
    replaced: bool,
    target: Mob | Player | None = None,
) -> GameEvent:
    text = f"You switch to {ability.name}." if replaced else f"You prepare {ability.name}."
    data: dict[str, Any] = {
        "ability": {
            "slug": ability.slug,
            "name": ability.name,
            "action_type": ability.action_type,
        }
    }
    if isinstance(target, Mob):
        data["target"] = serialize_char_from_mob(target).model_dump()
    elif isinstance(target, Player):
        data["target"] = serialize_char_from_player(target).model_dump()
    return GameEvent(
        type="cmd.ability.success",
        recipients=[player.key],
        data=data,
        text=text,
    )


def _active_player_encounter(player: Player) -> CombatEncounter | None:
    return (
        CombatEncounter.objects.select_for_update()
        .filter(player=player, status=CombatEncounter.STATUS_ACTIVE)
        .first()
    )


def _pending_payload(
    *,
    ability: AbilityDefinition,
    command: str,
    target_type: str,
    target_id: int,
    queued_round: int,
) -> dict[str, Any]:
    return {
        "ability": ability.slug,
        "command": command,
        "target": {
            "type": target_type,
            "id": target_id,
        },
        "queued_round": queued_round,
    }


def _combat_interval(config) -> float:
    if not config:
        return 0.0
    try:
        return float(config.combat_resolution_interval or 0)
    except (TypeError, ValueError):
        return 0.0


class LearnAbilityAction:
    def execute(self, player_id: int, selector: str | None) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().select_related("world").get(pk=player_id)
            ability = resolve_ability_for_selector(player.world, selector)
            if not ability:
                raise ActionError("Learn what ability?", code="ability_missing")

            available, reason = ability_is_available_to_player(player, ability)
            if not available:
                raise ActionError(reason, code="ability_unavailable")

            known = known_ability_slugs(player)
            trainer = None
            assigned_hotkey = None
            if ability.slug in known:
                text = f"You already know {ability.name}."
            else:
                trainer = require_trainer_for_ability_change(
                    player,
                    ability,
                    verb="learn",
                )
                max_known = max_known_abilities_for_world(player.world)
                if max_known is not None and len(known) >= max_known:
                    raise ActionError(
                        f"You can only know {max_known} abilities. Unlearn one first.",
                        code="known_ability_cap",
                        data={"max_known": max_known},
                    )
                known.append(ability.slug)
                player.known_abilities = known
                assigned_hotkey = _assign_next_ability_hotkey(player, ability)
                update_fields = ["known_abilities"]
                if assigned_hotkey:
                    update_fields.append("ability_hotkeys")
                    text = f"You learn {ability.name} and assign it to hotkey {assigned_hotkey}."
                else:
                    text = f"You learn {ability.name}."
                player.save(update_fields=update_fields)

        return ActionResult(events=[
            GameEvent(
                type="cmd.ability.learn.success",
                recipients=[player.key],
                data={
                    "ability": {
                        "slug": ability.slug,
                        "name": ability.name,
                        "hotkey": assigned_hotkey,
                    },
                    "trainer": (
                        {"id": trainer.id, "name": trainer.name}
                        if trainer else None
                    ),
                    "actor": ability_state_payload(player),
                },
                text=text,
            )
        ])


class UnlearnAbilityAction:
    def execute(self, player_id: int, selector: str | None) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().select_related("world").get(pk=player_id)
            ability = resolve_ability_for_selector(player.world, selector)
            if not ability:
                raise ActionError("Unlearn what ability?", code="ability_missing")

            known = known_ability_slugs(player)
            trainer = None
            if ability.slug not in known:
                text = f"You do not know {ability.name}."
            else:
                trainer = require_trainer_for_ability_change(
                    player,
                    ability,
                    verb="unlearn",
                )
                known.remove(ability.slug)
                player.known_abilities = known
                hotkey_removed = _remove_ability_hotkey(player, ability)
                update_fields = ["known_abilities"]
                if hotkey_removed:
                    update_fields.append("ability_hotkeys")
                player.save(update_fields=update_fields)
                CombatEncounter.objects.filter(
                    player=player,
                    status=CombatEncounter.STATUS_ACTIVE,
                    pending_player_ability__ability=ability.slug,
                ).update(pending_player_ability={})
                text = f"You unlearn {ability.name}."

        return ActionResult(events=[
            GameEvent(
                type="cmd.ability.unlearn.success",
                recipients=[player.key],
                data={
                    "ability": {"slug": ability.slug, "name": ability.name},
                    "trainer": (
                        {"id": trainer.id, "name": trainer.name}
                        if trainer else None
                    ),
                    "actor": ability_state_payload(player),
                },
                text=text,
            )
        ])


class SetAbilityHotkeyAction:
    def execute(self, player_id: int, hotkey: str | int | None, selector: str | None) -> ActionResult:
        try:
            slot_number = int(hotkey)
        except (TypeError, ValueError):
            raise ActionError(
                f"Choose a hotkey from 1 to {MAX_ABILITY_HOTKEY}.",
                code="invalid_hotkey",
                data={"max_hotkey": MAX_ABILITY_HOTKEY},
            )
        if slot_number < 1 or slot_number > MAX_ABILITY_HOTKEY:
            raise ActionError(
                f"Choose a hotkey from 1 to {MAX_ABILITY_HOTKEY}.",
                code="invalid_hotkey",
                data={"max_hotkey": MAX_ABILITY_HOTKEY},
            )

        with transaction.atomic():
            player = Player.objects.select_for_update().select_related("world").get(pk=player_id)
            ability = resolve_ability_for_selector(player.world, selector)
            if not ability:
                raise ActionError("Set the hotkey to what ability?", code="ability_missing")
            if not player_knows_ability(player, ability):
                raise ActionError(f"You do not know {ability.name}.", code="ability_unknown")

            slot = str(slot_number)
            hotkeys = ability_hotkeys(player)
            replaced_slug = hotkeys.get(slot)
            previous_slot = None
            for existing_slot, existing_slug in list(hotkeys.items()):
                if existing_slug == ability.slug:
                    previous_slot = existing_slot
                    del hotkeys[existing_slot]
            hotkeys[slot] = ability.slug
            player.ability_hotkeys = hotkeys
            player.save(update_fields=["ability_hotkeys"])

            replaced_ability = None
            if replaced_slug and replaced_slug != ability.slug:
                replaced_ability = _ability_queryset_for_world(player.world).filter(slug=replaced_slug).first()

            text = f"{ability.name} is now on hotkey {slot}."

        data: dict[str, Any] = {
            "ability": {"slug": ability.slug, "name": ability.name},
            "hotkey": slot,
            "actor": ability_state_payload(player),
        }
        if previous_slot and previous_slot != slot:
            data["previous_hotkey"] = previous_slot
        if replaced_ability:
            data["replaced_ability"] = {
                "slug": replaced_ability.slug,
                "name": replaced_ability.name,
            }
        return ActionResult(events=[
            GameEvent(
                type="cmd.ability.hotkey.success",
                recipients=[player.key],
                data=data,
                text=text,
            )
        ])


class AbilityAction:
    def _resolve_self_utility(self, *, player: Player, ability: AbilityDefinition) -> ActionResult:
        validate_ability_ready(player, ability)
        paid = pay_ability_cost(player, ability)

        stats = compute_stats(player.level, player.archetype, char=player, world=player.world)
        health_max = max(1, int(stats.get("health_max") or 1))
        player.health = min(int(player.health or 0), health_max)

        events: list[GameEvent] = []
        hit_landed = False
        for component in ability.components or []:
            component_type = component.get("type")
            if component_type == "state":
                state_event = execute_state_component(
                    component=component,
                    player=player,
                    ability=ability,
                    room=getattr(player, "room", None),
                    hit_landed=hit_landed,
                )
                if state_event:
                    events.append(state_event)
                continue

            if component_type != "healing":
                continue
            result = resolve_attack(
                actor=player,
                target=player,
                world=player.world,
                profile_key=component.get("profile"),
                overrides=ability_component_overrides(
                    component,
                    player=player,
                    ability=ability,
                    room=getattr(player, "room", None),
                ),
            )
            if result.healing_done > 0:
                player.health = min(health_max, int(player.health or 0) + result.healing_done)
                hit_landed = True
            label = (component.get("text") or {}).get("label") or ability.name
            data = {
                "ability": ability.slug,
                "label": label,
                "actor": serialize_char_from_player(player).model_dump(),
                "target": serialize_char_from_player(player).model_dump(),
            }
            data.update(result.event_data())
            events.append(
                GameEvent(
                    type="notification.ability.heal",
                    recipients=[player.key],
                    data=data,
                    text=f"You use {ability.name} and heal for {result.healing_done}.",
                )
            )

        cooldown_started = start_ability_cooldown(player, ability)
        update_fields = ["health"]
        if paid:
            update_fields.append((ability.cost or {}).get("resource") or "energy")
        if cooldown_started:
            update_fields.append("ability_cooldowns")
        player.save(update_fields=list(dict.fromkeys(update_fields)))
        if cooldown_started:
            events.append(ability_state_event(player))
        return ActionResult(events=events)

    def _resolve_immediately(
        self,
        *,
        player: Player,
        target_mob: Mob,
        ability: AbilityDefinition,
        command: str,
        config,
    ) -> ActionResult:
        from spawns.actions import combat as combat_actions

        events: list[GameEvent] = [
            _ability_ack(player=player, ability=ability, replaced=False, target=target_mob)
        ]
        room = Room.objects.select_related("world", "zone").get(pk=player.room_id)
        for round_no in range(1, combat_actions.MAX_AUTO_RESOLVE_ROUNDS + 1):
            encounter = CombatEncounter(
                world=player.world,
                room=room,
                player=player,
                mob=target_mob,
                resolution_interval=0,
                round_number=round_no - 1,
                pending_player_ability=(
                    _pending_payload(
                        ability=ability,
                        command=command,
                        target_type="mob",
                        target_id=target_mob.id,
                        queued_round=round_no - 1,
                    )
                    if round_no == 1
                    else {}
                ),
            )
            step = combat_actions._apply_encounter_round(
                encounter=encounter,
                player=player,
                target_mob=target_mob,
                config=config,
            )
            events.extend(step.events)
            if not step.encounter_active:
                return ActionResult(events=events)
            target_mob.refresh_from_db()
            player.refresh_from_db()

        raise ActionError("Combat stalled before anyone died.", code="combat_stalled")

    def execute(
        self,
        player_id: int,
        *,
        ability: AbilityDefinition,
        command: str,
        args: list[str],
    ) -> ActionResult:
        with transaction.atomic():
            player = Player.objects.select_for_update().select_related("world").get(pk=player_id)
            if not player.room_id:
                raise ActionError("You are nowhere.", code="no_room")

            config = player.world.effective_config
            if config and not config.allow_combat and ability.target.get("type") == "hostile":
                raise ActionError("Combat is disabled here.", code="combat_disabled")

            validate_ability_ready(player, ability)

            target_type = (ability.target or {}).get("type") or "hostile"
            active_encounter = _active_player_encounter(player)
            target_selector = " ".join(args).strip()

            if target_type in {"self", "ally"}:
                if active_encounter:
                    replaced = bool(active_encounter.pending_player_ability)
                    active_encounter.pending_player_ability = _pending_payload(
                        ability=ability,
                        command=command,
                        target_type="player",
                        target_id=player.id,
                        queued_round=active_encounter.round_number,
                    )
                    active_encounter.save(update_fields=["pending_player_ability"])
                    if active_encounter.resolution_interval == -1:
                        from spawns.actions.combat import resolve_combat_encounter_step

                        step = resolve_combat_encounter_step(active_encounter.id, auto_advance=False)
                        return ActionResult(events=[
                            _ability_ack(player=player, ability=ability, replaced=replaced, target=player),
                            *step.events,
                        ])
                    return ActionResult(events=[
                        _ability_ack(player=player, ability=ability, replaced=replaced, target=player)
                    ])

                if not (ability.target or {}).get("allow_out_of_combat", True):
                    raise ActionError(f"{ability.name} can only be used in combat.", code="combat_required")
                return self._resolve_self_utility(player=player, ability=ability)

            room = Room.objects.select_related("world", "zone").get(pk=player.room_id)
            if active_encounter:
                if not active_encounter.mob_id:
                    raise ActionError("You are already in combat.", code="combat_in_progress")
                target_mob = (
                    Mob.objects.select_for_update()
                    .filter(pk=active_encounter.mob_id, is_pending_deletion=False)
                    .first()
                )
                if not target_mob:
                    raise ActionError("Your current target is gone.", code="target_missing")
                if target_selector:
                    target_ref = resolve_room_mob_target(
                        room,
                        target_selector,
                        empty_error="Use the ability on what?",
                        not_found_error="You don't see them here.",
                    )
                    if target_ref.id != active_encounter.mob_id:
                        raise ActionError(
                            f"You are already fighting {target_mob.name or 'them'}.",
                            code="combat_in_progress",
                        )
                replaced = bool(active_encounter.pending_player_ability)
                active_encounter.pending_player_ability = _pending_payload(
                    ability=ability,
                    command=command,
                    target_type="mob",
                    target_id=active_encounter.mob_id,
                    queued_round=active_encounter.round_number,
                )
                active_encounter.save(update_fields=["pending_player_ability"])
                if active_encounter.resolution_interval == -1:
                    from spawns.actions.combat import resolve_combat_encounter_step

                    step = resolve_combat_encounter_step(active_encounter.id, auto_advance=False)
                    return ActionResult(events=[
                        _ability_ack(player=player, ability=ability, replaced=replaced, target=target_mob),
                        *step.events,
                    ])
                return ActionResult(events=[
                    _ability_ack(player=player, ability=ability, replaced=replaced, target=target_mob)
                ])

            target_ref = resolve_room_mob_target(
                room,
                target_selector,
                empty_error="Use the ability on what?",
                not_found_error="You don't see them here.",
            )
            target_mob = (
                Mob.objects.select_for_update()
                .filter(pk=target_ref.id, is_pending_deletion=False)
                .first()
            )
            if not target_mob:
                raise ActionError("You don't see them here.", code="target_missing")

            if CombatEncounter.objects.select_for_update().filter(
                mob=target_mob,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists():
                raise ActionError(
                    f"{target_mob.name or 'They'} are already fighting someone else.",
                    code="target_busy",
                )

            interval = _combat_interval(config)
            if interval == 0:
                return self._resolve_immediately(
                    player=player,
                    target_mob=target_mob,
                    ability=ability,
                    command=command,
                    config=config,
                )

            encounter = CombatEncounter.objects.create(
                world=player.world,
                room=room,
                player=player,
                mob=target_mob,
                resolution_interval=interval,
                pending_player_ability=_pending_payload(
                    ability=ability,
                    command=command,
                    target_type="mob",
                    target_id=target_mob.id,
                    queued_round=0,
                ),
                next_resolution_ts=(
                    timezone.now() + timedelta(seconds=interval)
                    if interval > 0
                    else None
                ),
            )

            if interval == -1:
                from spawns.actions.combat import resolve_combat_encounter_step

                step = resolve_combat_encounter_step(encounter.id, auto_advance=False)
                return ActionResult(events=[
                    _ability_ack(player=player, ability=ability, replaced=False, target=target_mob),
                    *step.events,
                ])

            from spawns.actions.combat import _schedule_encounter_resolution

            _schedule_encounter_resolution(encounter.id, interval)
            return ActionResult(events=[
                _ability_ack(player=player, ability=ability, replaced=False, target=target_mob)
            ])
