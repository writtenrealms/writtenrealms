from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from builders.models import (
    ItemBundle,
    ItemDefinition,
    ItemTemplate,
    MobDefinition,
    MobTemplate,
    Path,
    SpawnEntry,
    SpawnPlacement,
    SpawnPlan,
    SpawnPlanRun,
)
from config import constants as adv_consts
from core.condition_dsl import ConditionContext, evaluate_condition
from worlds.models import Room, World, Zone


SOURCE_MODELS = {
    "itembundle": ItemBundle,
    "item_bundle": ItemBundle,
    "itemdefinition": ItemDefinition,
    "item_definition": ItemDefinition,
    "itemtemplate": ItemTemplate,
    "item_template": ItemTemplate,
    "mobdefinition": MobDefinition,
    "mob_definition": MobDefinition,
    "mobtemplate": MobTemplate,
    "mob_template": MobTemplate,
}

NUMERIC_MODIFIER_FIELDS = {
    "ability_power",
    "armor",
    "attack_power",
    "cost",
    "crit",
    "dodge",
    "energy_max",
    "energy_regen",
    "exp_worth",
    "food_value",
    "gold",
    "health_max",
    "health_regen",
    "level",
    "resilience",
    "stamina_max",
    "stamina_regen",
    "weapon_damage",
}

CURRENT_RESOURCE_FIELDS = {
    "energy_max": "energy",
    "health_max": "health",
    "stamina_max": "stamina",
}


@dataclass(frozen=True)
class ResolvedSource:
    source_type: str
    source_slug: str
    source_id: int
    source: Any


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _plan_spec_hash(plan: SpawnPlan) -> str:
    entries = [
        {
            "slug": entry.slug,
            "order": entry.order,
            "is_active": entry.is_active,
            "source": entry.source,
            "target": entry.target,
            "count": entry.count,
            "placement": entry.placement,
            "affixes": entry.affixes,
            "conditions": entry.conditions,
        }
        for entry in plan.entries.all().order_by("order", "created_ts", "id")
    ]
    payload = {
        "slug": plan.slug,
        "zone_id": plan.zone_id,
        "reset_policy": plan.reset_policy,
        "respawn_policy": plan.respawn_policy,
        "randomization": plan.randomization,
        "conditions": plan.conditions,
        "entries": entries,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _seed_for_plan(*, spawn_world: World, plan: SpawnPlan, spec_hash: str) -> str:
    seed_scope = str((plan.randomization or {}).get("seed_scope") or "instance").strip().lower()
    if seed_scope == "world":
        seed_basis = f"world:{plan.world_id}:{plan.slug}:{spec_hash}"
    elif seed_scope == "explicit":
        explicit_seed = str((plan.randomization or {}).get("seed") or "").strip()
        seed_basis = explicit_seed or f"explicit:{plan.world_id}:{plan.slug}:{spec_hash}"
    else:
        seed_basis = f"instance:{spawn_world.id}:{plan.world_id}:{plan.slug}:{spec_hash}"
    return hashlib.sha256(seed_basis.encode("utf-8")).hexdigest()


def _parse_key_ref(value: Any, *, expected_prefixes: set[str] | None = None) -> tuple[str | None, str | None]:
    text = str(value or "").strip()
    if not text or "." not in text:
        return None, None
    prefix, _, raw_value = text.partition(".")
    normalized_prefix = prefix.strip().lower().replace("-", "_")
    if expected_prefixes and normalized_prefix not in expected_prefixes:
        return None, None
    ref_value = raw_value.strip()
    return normalized_prefix, ref_value


def _resolve_zone(*, world: World, value: Any, field_name: str = "spec.zone") -> Zone:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    prefix, ref_value = _parse_key_ref(text, expected_prefixes={"zone"})
    if prefix == "zone" and ref_value and ref_value.isdigit():
        zone = Zone.objects.filter(world=world, pk=int(ref_value)).first()
    else:
        zone = Zone.objects.filter(world=world, name=text).order_by("id").first()
    if zone is None:
        raise serializers.ValidationError(f"{field_name} does not resolve to a zone in this world.")
    return zone


def _resolve_room(*, world: World, value: Any, field_name: str = "room") -> Room:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if text.startswith("room@"):
        raw_coords = text[len("room@"):]
        parts = [part.strip() for part in raw_coords.split(",")]
        if len(parts) != 3:
            raise serializers.ValidationError(f"{field_name} must use room@x,y,z.")
        try:
            x, y, z = [int(part) for part in parts]
        except ValueError:
            raise serializers.ValidationError(f"{field_name} must use integer room coordinates.")
        room = Room.objects.filter(world=world, x=x, y=y, z=z).first()
    else:
        prefix, ref_value = _parse_key_ref(text, expected_prefixes={"room"})
        if prefix == "room" and ref_value and ref_value.isdigit():
            room = Room.objects.filter(world=world, pk=int(ref_value)).first()
        else:
            room = Room.objects.filter(world=world, name=text).order_by("id").first()
    if room is None:
        raise serializers.ValidationError(f"{field_name} does not resolve to a room in this world.")
    return room


def _resolve_path(*, world: World, value: Any, field_name: str = "path") -> Path:
    text = str(value or "").strip()
    if not text:
        raise serializers.ValidationError(f"{field_name} is required.")
    if not text.startswith("path@"):
        raise serializers.ValidationError(f"{field_name} must use path@<relative_id>.")
    raw_relative_id = text[len("path@"):].strip()
    try:
        relative_id = int(raw_relative_id)
    except ValueError:
        raise serializers.ValidationError(f"{field_name} must use integer path@<relative_id>.")
    if relative_id <= 0:
        raise serializers.ValidationError(f"{field_name} relative id must be positive.")
    path = Path.objects.filter(world=world, relative_id=relative_id).first()
    if path is None:
        raise serializers.ValidationError(f"{field_name} does not resolve to a path in this world.")
    return path


def _source_ref_from_value(source_spec: Any) -> str:
    if isinstance(source_spec, str):
        return source_spec
    if isinstance(source_spec, dict):
        return str(source_spec.get("ref") or source_spec.get("source") or "").strip()
    return ""


def _choose_source_spec(entry: SpawnEntry, rng: random.Random) -> Any:
    source_pool = entry.source.get("pool") if isinstance(entry.source, dict) else None
    if source_pool is None and isinstance(entry.source, dict):
        source_pool = entry.source.get("source_pool")
    if source_pool is None:
        return entry.source
    if not isinstance(source_pool, list) or not source_pool:
        raise serializers.ValidationError(f"Spawn entry '{entry.slug}' source_pool must be a non-empty list.")
    total = 0
    weighted: list[tuple[Any, int]] = []
    for item in source_pool:
        try:
            weight = int(item.get("weight", 1)) if isinstance(item, dict) else 1
        except (TypeError, ValueError):
            raise serializers.ValidationError(f"Spawn entry '{entry.slug}' source_pool weight must be an integer.")
        if weight <= 0:
            continue
        weighted.append((item, weight))
        total += weight
    if not weighted:
        raise serializers.ValidationError(f"Spawn entry '{entry.slug}' source_pool has no positive weights.")
    target = rng.randint(1, total)
    cumulative = 0
    for item, weight in weighted:
        cumulative += weight
        if target <= cumulative:
            return item
    return weighted[-1][0]


def resolve_source(*, world: World, source_spec: Any, field_name: str = "source") -> ResolvedSource:
    source_ref = _source_ref_from_value(source_spec)
    prefix, ref_value = _parse_key_ref(source_ref)
    if prefix not in SOURCE_MODELS or not ref_value:
        raise serializers.ValidationError(
            f"{field_name} must use a supported ref such as mobdefinition.slug or itemdefinition.slug."
        )
    model_cls = SOURCE_MODELS[prefix]
    queryset = model_cls.objects.filter(world=world)
    if ref_value.isdigit():
        source = queryset.filter(pk=int(ref_value)).first()
    else:
        source = queryset.filter(slug=ref_value).first()
    if source is None:
        raise serializers.ValidationError(f"{field_name} does not resolve to authored content in this world.")
    source_slug = getattr(source, "slug", "") or str(source.pk)
    canonical_type = {
        ItemBundle: "itembundle",
        ItemDefinition: "itemdefinition",
        ItemTemplate: "itemtemplate",
        MobDefinition: "mobdefinition",
        MobTemplate: "mobtemplate",
    }[model_cls]
    return ResolvedSource(
        source_type=canonical_type,
        source_slug=source_slug,
        source_id=source.id,
        source=source,
    )


def _entry_count(entry: SpawnEntry, rng: random.Random) -> int:
    count_spec = entry.count
    if isinstance(count_spec, int):
        return max(0, count_spec)
    if isinstance(count_spec, str) and count_spec.strip().isdigit():
        return max(0, int(count_spec.strip()))
    if isinstance(count_spec, dict):
        if "value" in count_spec:
            try:
                return max(0, int(count_spec.get("value") or 0))
            except (TypeError, ValueError):
                raise serializers.ValidationError(f"Spawn entry '{entry.slug}' count.value must be an integer.")
        if "min" in count_spec or "max" in count_spec:
            try:
                minimum = max(0, int(count_spec.get("min", 0)))
                maximum = max(minimum, int(count_spec.get("max", minimum)))
            except (TypeError, ValueError):
                raise serializers.ValidationError(f"Spawn entry '{entry.slug}' count min/max must be integers.")
            return rng.randint(minimum, maximum)
    return 1


def _rooms_for_zone_target(zone: Zone, *, source_type: str) -> list[Room]:
    rooms_qs = zone.rooms.all()
    if source_type.startswith("mob"):
        rooms_qs = rooms_qs.exclude(type=adv_consts.ROOM_TYPE_WATER).exclude(
            flags__code=adv_consts.ROOM_FLAG_NO_ROAM,
        )
    else:
        rooms_qs = rooms_qs.exclude(type=adv_consts.ROOM_TYPE_WATER)
    rooms_qs = rooms_qs.exclude(flags__code=adv_consts.ROOM_FLAG_NO_LOAD)
    return list(rooms_qs.order_by("z", "y", "x", "id"))


def _rooms_for_path_target(path: Path, *, source_type: str) -> list[Room]:
    if path.entry_room_id:
        return [path.entry_room]
    rooms_qs = path.rooms.all()
    if source_type.startswith("mob"):
        rooms_qs = rooms_qs.exclude(flags__code=adv_consts.ROOM_FLAG_NO_ROAM)
    rooms_qs = rooms_qs.exclude(flags__code=adv_consts.ROOM_FLAG_NO_LOAD)
    if not source_type.startswith("mob"):
        rooms_qs = rooms_qs.exclude(type=adv_consts.ROOM_TYPE_WATER)
    return list(rooms_qs.order_by("z", "y", "x", "id"))


def _target_mapping(entry: SpawnEntry) -> dict[str, Any]:
    target = entry.target
    if isinstance(target, str):
        return {"room": target}
    if isinstance(target, dict):
        return target
    return {}


def _choose_room_for_entry(
    *,
    world: World,
    entry: SpawnEntry,
    source_type: str,
    rng: random.Random,
) -> tuple[Room, dict[str, Any]]:
    target = _target_mapping(entry)
    if target.get("room"):
        room = _resolve_room(world=world, value=target.get("room"), field_name=f"entries.{entry.slug}.target.room")
        return room, {"target_type": "room", "target_id": room.id}
    if target.get("room_ref"):
        room = _resolve_room(world=world, value=target.get("room_ref"), field_name=f"entries.{entry.slug}.target.room_ref")
        return room, {"target_type": "room", "target_id": room.id}
    if target.get("zone"):
        zone = _resolve_zone(world=world, value=target.get("zone"), field_name=f"entries.{entry.slug}.target.zone")
        rooms = _rooms_for_zone_target(zone, source_type=source_type)
        if not rooms:
            raise serializers.ValidationError(f"Spawn entry '{entry.slug}' zone target has no eligible rooms.")
        room = rooms[rng.randrange(0, len(rooms))]
        return room, {"target_type": "zone", "target_id": zone.id}
    if target.get("path"):
        path = _resolve_path(world=world, value=target.get("path"), field_name=f"entries.{entry.slug}.target.path")
        rooms = _rooms_for_path_target(path, source_type=source_type)
        if not rooms:
            raise serializers.ValidationError(f"Spawn entry '{entry.slug}' path target has no eligible rooms.")
        room = rooms[rng.randrange(0, len(rooms))]
        return room, {"target_type": "path", "target_id": path.id}
    raise serializers.ValidationError(f"Spawn entry '{entry.slug}' must target a room, zone, path, or entry.")


def _target_entry_slug(entry: SpawnEntry) -> str:
    target = _target_mapping(entry)
    raw_target = target.get("entry") or target.get("parent_entry")
    return str(raw_target or "").strip()


def _entry_conditions_pass(*, spawn_world: World, plan: SpawnPlan, entry: SpawnEntry) -> bool:
    if not entry.conditions:
        return True
    return evaluate_condition(
        entry.conditions,
        context=ConditionContext(
            actor=spawn_world,
            world=spawn_world,
            zone=plan.zone,
        ),
    )


def _plan_conditions_pass(*, spawn_world: World, plan: SpawnPlan) -> bool:
    if not plan.conditions:
        return True
    return evaluate_condition(
        plan.conditions,
        context=ConditionContext(
            actor=spawn_world,
            world=spawn_world,
            zone=plan.zone,
        ),
    )


def _generate_affixes(entry: SpawnEntry, rng: random.Random) -> tuple[list[str], dict[str, Any]]:
    affixes = entry.affixes if isinstance(entry.affixes, dict) else {}
    selected = list(affixes.get("guaranteed") or [])
    try:
        chance = int(affixes.get("chance", 0) or 0)
    except (TypeError, ValueError):
        raise serializers.ValidationError(f"Spawn entry '{entry.slug}' affixes.chance must be an integer.")
    pool = affixes.get("pool") or []
    modifiers: dict[str, Any] = {}
    if chance > 0 and pool and rng.randint(1, 100) <= chance:
        weighted = []
        total = 0
        for option in pool:
            if not isinstance(option, dict):
                continue
            try:
                weight = int(option.get("weight", 1) or 1)
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    f"Spawn entry '{entry.slug}' affix pool weights must be integers."
                )
            if weight <= 0:
                continue
            weighted.append((option, weight))
            total += weight
        if weighted:
            target = rng.randint(1, total)
            cumulative = 0
            for option, weight in weighted:
                cumulative += weight
                if target <= cumulative:
                    key = str(option.get("key") or "").strip()
                    if key:
                        selected.append(key)
                    if isinstance(option.get("modifiers"), dict):
                        modifiers.update(option["modifiers"])
                    break
    return selected, modifiers


def _active_run_for_plan(*, spawn_world: World, plan: SpawnPlan, initial: bool = False) -> SpawnPlanRun:
    spec_hash = _plan_spec_hash(plan)
    seed = _seed_for_plan(spawn_world=spawn_world, plan=plan, spec_hash=spec_hash)
    run = SpawnPlanRun.objects.filter(
        spawn_world=spawn_world,
        plan=plan,
        status=SpawnPlanRun.STATUS_ACTIVE,
    ).order_by("-created_ts", "-id").first()
    if run is None:
        run = SpawnPlanRun.objects.create(
            spawn_world=spawn_world,
            plan=plan,
            seed=seed,
            spec_hash=spec_hash,
        )
    elif initial and run.spec_hash != spec_hash:
        run.placements.all().delete()
        run.seed = seed
        run.spec_hash = spec_hash
        run.generated_at = timezone.now()
        run.save(update_fields=["seed", "spec_hash", "generated_at", "modified_ts"])
    return run


def _generate_placements_for_run(*, run: SpawnPlanRun) -> None:
    if run.placements.exists():
        return
    plan = run.plan
    rng = random.Random(run.seed)
    generated_by_entry: dict[str, list[SpawnPlacement]] = {}
    for entry in plan.entries.filter(is_active=True).order_by("order", "created_ts", "id"):
        parent_entry_slug = _target_entry_slug(entry)
        count = _entry_count(entry, rng)
        created: list[SpawnPlacement] = []
        if parent_entry_slug:
            parents = generated_by_entry.get(parent_entry_slug, [])
            for parent in parents:
                for nested_index in range(count):
                    source_spec = _choose_source_spec(entry, rng)
                    source = resolve_source(
                        world=plan.world,
                        source_spec=source_spec,
                        field_name=f"entries.{entry.slug}.source",
                    )
                    affixes, modifiers = _generate_affixes(entry, rng)
                    created.append(
                        SpawnPlacement.objects.create(
                            run=run,
                            entry_slug=entry.slug,
                            slot_index=len(created),
                            room=parent.room,
                            source_type=source.source_type,
                            source_slug=source.source_slug,
                            source_id=source.source_id,
                            parent_entry_slug=parent.entry_slug,
                            parent_slot_index=parent.slot_index,
                            affixes=affixes,
                            modifiers=modifiers,
                            state={"target_type": "entry"},
                        )
                    )
        else:
            for slot_index in range(count):
                source_spec = _choose_source_spec(entry, rng)
                source = resolve_source(
                    world=plan.world,
                    source_spec=source_spec,
                    field_name=f"entries.{entry.slug}.source",
                )
                room, state = _choose_room_for_entry(
                    world=plan.world,
                    entry=entry,
                    source_type=source.source_type,
                    rng=rng,
                )
                affixes, modifiers = _generate_affixes(entry, rng)
                created.append(
                    SpawnPlacement.objects.create(
                        run=run,
                        entry_slug=entry.slug,
                        slot_index=slot_index,
                        room=room,
                        source_type=source.source_type,
                        source_slug=source.source_slug,
                        source_id=source.source_id,
                        affixes=affixes,
                        modifiers=modifiers,
                        state=state,
                    )
                )
        generated_by_entry[entry.slug] = created


def _placement_source(placement: SpawnPlacement):
    model_cls = SOURCE_MODELS.get(placement.source_type)
    if model_cls is None:
        return None
    return model_cls.objects.filter(pk=placement.source_id).first()


def _rng_for_placement(placement: SpawnPlacement) -> random.Random:
    seed = (
        f"{placement.run.seed}:"
        f"{placement.entry_slug}:"
        f"{placement.slot_index}:"
        f"{placement.source_type}:"
        f"{placement.source_slug}"
    )
    return random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())


def _apply_spawn_origin_metadata(entity: Any, placement: SpawnPlacement) -> None:
    metadata = getattr(entity, "roll_metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["spawn_plan"] = {
        "plan_id": placement.run.plan_id,
        "plan_slug": placement.run.plan.slug,
        "run_id": placement.run_id,
        "placement_id": placement.id,
        "entry_slug": placement.entry_slug,
        "slot_index": placement.slot_index,
        "source": f"{placement.source_type}.{placement.source_slug}",
        "affixes": list(placement.affixes or []),
        "modifiers": dict(placement.modifiers or {}),
    }
    entity.roll_metadata = metadata


def _numeric_modifier_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _minimum_for_numeric_field(field_name: str) -> int:
    if field_name in {"health_max", "level"}:
        return 1
    return 0


def _apply_spawn_modifiers(entity: Any, placement: SpawnPlacement) -> list[str]:
    modifiers = placement.modifiers if isinstance(placement.modifiers, dict) else {}
    update_fields = []
    for raw_key, raw_value in modifiers.items():
        key = str(raw_key or "").strip()
        multiplier = False
        if key.endswith("_multiplier"):
            field_name = key[: -len("_multiplier")]
            multiplier = True
        else:
            field_name = key
        if field_name not in NUMERIC_MODIFIER_FIELDS or not hasattr(entity, field_name):
            continue
        modifier_value = _numeric_modifier_value(raw_value)
        current_value = _numeric_modifier_value(getattr(entity, field_name, None))
        if modifier_value is None or current_value is None:
            continue
        if multiplier:
            next_value = int(round(current_value * modifier_value))
        else:
            next_value = int(round(current_value + modifier_value))
        next_value = max(_minimum_for_numeric_field(field_name), next_value)
        setattr(entity, field_name, next_value)
        update_fields.append(field_name)
        resource_field = CURRENT_RESOURCE_FIELDS.get(field_name)
        if resource_field and hasattr(entity, resource_field):
            setattr(entity, resource_field, next_value)
            update_fields.append(resource_field)
    return list(dict.fromkeys(update_fields))


def _placement_roams(placement: SpawnPlacement):
    target_type = (placement.state or {}).get("target_type")
    target_id = (placement.state or {}).get("target_id")
    if target_type == "zone":
        return Zone.objects.filter(pk=target_id).first()
    if target_type == "path":
        return Path.objects.filter(pk=target_id).first()
    return None


def _parent_instance(*, placement: SpawnPlacement, spawn_world: World):
    if not placement.parent_entry_slug:
        return None
    parent = SpawnPlacement.objects.filter(
        run=placement.run,
        entry_slug=placement.parent_entry_slug,
        slot_index=placement.parent_slot_index,
    ).first()
    if parent is None:
        return None
    from spawns.models import Item, Mob

    mob = Mob.objects.filter(
        world=spawn_world,
        spawn_placement=parent,
        is_pending_deletion=False,
    ).first()
    if mob is not None:
        return mob
    return Item.objects.filter(
        world=spawn_world,
        spawn_placement=parent,
        is_pending_deletion=False,
    ).first()


def _placement_has_live_output(*, placement: SpawnPlacement, spawn_world: World) -> bool:
    from spawns.models import Item, Mob

    if placement.source_type.startswith("mob"):
        return Mob.objects.filter(
            world=spawn_world,
            spawn_placement=placement,
            is_pending_deletion=False,
        ).exists()
    return Item.objects.filter(
        world=spawn_world,
        spawn_placement=placement,
        is_pending_deletion=False,
    ).exists()


def _materialize_placement(*, placement: SpawnPlacement, spawn_world: World):
    if _placement_has_live_output(placement=placement, spawn_world=spawn_world):
        return []
    source = _placement_source(placement)
    if source is None:
        return []
    if placement.parent_entry_slug:
        target = _parent_instance(placement=placement, spawn_world=spawn_world)
        if target is None:
            return []
    else:
        target = placement.room
    if placement.source_type.startswith("mob"):
        spawn_kwargs = {
            "target": placement.room,
            "spawn_world": spawn_world,
            "roams": _placement_roams(placement),
            "rule": None,
        }
        if placement.source_type == "mobdefinition":
            spawn_kwargs["rng"] = _rng_for_placement(placement)
        spawned = source.spawn(**spawn_kwargs)
        spawned.spawn_placement = placement
        _apply_spawn_origin_metadata(spawned, placement)
        modifier_fields = _apply_spawn_modifiers(spawned, placement)
        spawned.save(update_fields=[
            "spawn_placement",
            "roll_metadata",
            *modifier_fields,
            "modified_ts",
        ])
        return [spawned]
    spawn_kwargs = {
        "target": target,
        "spawn_world": spawn_world,
        "rule": None,
    }
    if placement.source_type in {"itemdefinition", "itembundle"}:
        spawn_kwargs["rng"] = _rng_for_placement(placement)
    spawned_items = source.spawn(**spawn_kwargs)
    if not isinstance(spawned_items, list):
        spawned_items = [spawned_items]
    for item in spawned_items:
        item.spawn_placement = placement
        _apply_spawn_origin_metadata(item, placement)
        modifier_fields = _apply_spawn_modifiers(item, placement)
        item.save(update_fields=[
            "spawn_placement",
            "roll_metadata",
            *modifier_fields,
            "modified_ts",
        ])
    return spawned_items


def _plan_is_due(*, run: SpawnPlanRun, initial: bool, repopulate: bool) -> bool:
    if initial or repopulate or run.last_reconciled_at is None:
        return True
    policy = run.plan.respawn_policy or {}
    mode = str(policy.get("mode") or "fixed").strip().lower()
    if mode == "none":
        return False
    seconds = policy.get("seconds")
    if seconds in (None, "") and mode == "inherit_zone":
        seconds = run.plan.zone.respawn_wait
    seconds = int(seconds or 0)
    if seconds == -1:
        return False
    if seconds == 0:
        return True
    return timezone.now() >= run.last_reconciled_at + timedelta(seconds=seconds)


def reconcile_spawn_plan(*, spawn_world: World, plan: SpawnPlan, initial: bool = False, repopulate: bool = False) -> dict[str, Any]:
    if not spawn_world.context:
        raise TypeError("Can only run spawn plans on spawn worlds.")
    with transaction.atomic():
        plan = SpawnPlan.objects.select_for_update().select_related("zone", "world").get(pk=plan.pk)
        if not plan.is_active:
            return {"plan": plan.slug, "placements": 0, "spawned": 0, "skipped": True}
        if not _plan_conditions_pass(spawn_world=spawn_world, plan=plan):
            return {"plan": plan.slug, "placements": 0, "spawned": 0, "skipped": True}
        run = _active_run_for_plan(spawn_world=spawn_world, plan=plan, initial=initial)
        run = SpawnPlanRun.objects.select_for_update().get(pk=run.pk)
        if not _plan_is_due(run=run, initial=initial, repopulate=repopulate):
            return {"plan": plan.slug, "placements": run.placements.count(), "spawned": 0, "skipped": True}
        _generate_placements_for_run(run=run)
        spawned_count = 0
        for placement in run.placements.select_related("room").order_by("entry_slug", "slot_index", "id"):
            entry = plan.entries.filter(slug=placement.entry_slug, is_active=True).first()
            if entry and not _entry_conditions_pass(spawn_world=spawn_world, plan=plan, entry=entry):
                continue
            spawned_count += len(_materialize_placement(placement=placement, spawn_world=spawn_world))
        run.last_reconciled_at = timezone.now()
        run.save(update_fields=["last_reconciled_at", "modified_ts"])
        return {
            "plan": plan.slug,
            "placements": run.placements.count(),
            "spawned": spawned_count,
            "skipped": False,
        }


def run_spawn_plans(*, world: World, zone_id: int | None = None, initial: bool = False, repopulate: bool = False) -> list[dict[str, Any]]:
    if not world.context:
        raise TypeError("Can only run spawn plans on spawn worlds.")
    plans = SpawnPlan.objects.filter(world=world.context, is_active=True).select_related("zone", "world")
    if zone_id:
        plans = plans.filter(zone_id=zone_id)
    results = []
    for plan in plans.order_by("zone__id", "order", "created_ts", "id"):
        results.append(
            reconcile_spawn_plan(
                spawn_world=world,
                plan=plan,
                initial=initial,
                repopulate=repopulate,
            )
        )
    return results
