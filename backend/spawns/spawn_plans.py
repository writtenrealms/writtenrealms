from __future__ import annotations

import hashlib
import json
import copy
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
    MobDefinition,
    Path,
    SpawnEntry,
    SpawnPlacement,
    SpawnPlan,
    SpawnPlanRun,
)
from builders.loot_tables import merge_loot_tables
from config import constants as adv_consts
from core.condition_dsl import ConditionContext, evaluate_condition
from core.mob_traits import (
    apply_numeric_modifiers,
    normalize_trait_table,
    trait_instances,
    trait_keys,
    trait_modifiers,
)
from worlds.models import Room, RoomFlag, World, Zone


SOURCE_MODELS = {
    "itembundle": ItemBundle,
    "item_bundle": ItemBundle,
    "itemdefinition": ItemDefinition,
    "item_definition": ItemDefinition,
    "mobdefinition": MobDefinition,
    "mob_definition": MobDefinition,
}

INHERITED_INSTANCE_SOURCE_MODELS = {ItemBundle, ItemDefinition, MobDefinition}
COHORT_RESPAWN_REFILL_MISSING = "refill_missing"
COHORT_RESPAWN_POLICIES = {COHORT_RESPAWN_REFILL_MISSING}


@dataclass
class SpawnReconcileContext:
    """Lazily cache shared lookups for one spawn-world reconciliation."""

    authored_world_id: int
    spawn_world_id: int
    _no_roam_room_ids: set[int] | None = None
    _live_output_placement_ids: set[int] | None = None

    def room_is_no_roam(self, room_id: int) -> bool:
        if self._no_roam_room_ids is None:
            self._no_roam_room_ids = set(
                RoomFlag.objects.filter(
                    room__world_id=self.authored_world_id,
                    code=adv_consts.ROOM_FLAG_NO_ROAM,
                ).values_list("room_id", flat=True)
            )
        return room_id in self._no_roam_room_ids

    def placement_has_live_output(self, placement_id: int) -> bool:
        if self._live_output_placement_ids is None:
            from spawns.models import Item, Mob

            mob_placement_ids = Mob.objects.filter(
                world_id=self.spawn_world_id,
                spawn_placement_id__isnull=False,
                is_pending_deletion=False,
            ).values_list("spawn_placement_id", flat=True)
            item_placement_ids = Item.objects.filter(
                world_id=self.spawn_world_id,
                spawn_placement_id__isnull=False,
                is_pending_deletion=False,
            ).values_list("spawn_placement_id", flat=True)
            self._live_output_placement_ids = {
                *mob_placement_ids,
                *item_placement_ids,
            }
        return placement_id in self._live_output_placement_ids


@dataclass(frozen=True)
class ResolvedSource:
    source_type: str
    source_slug: str
    source_id: int
    source: Any


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _spec_digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _plan_spec_hash(plan: SpawnPlan, *, entries: list[SpawnEntry] | None = None) -> str:
    if entries is None:
        entries = list(plan.entries.all().order_by("order", "created_ts", "id"))
    entries = [
        {
            "slug": entry.slug,
            "order": entry.order,
            "is_active": entry.is_active,
            "source": entry.source,
            "target": entry.target,
            "count": entry.count,
            "placement": entry.placement,
            "traits": entry.traits,
            "loot": entry.loot,
            "conditions": entry.conditions,
        }
        for entry in entries
    ]
    payload = {
        "slug": plan.slug,
        "zone_id": plan.zone_id,
        "respawn_policy": plan.respawn_policy,
        "randomization": plan.randomization,
        "conditions": plan.conditions,
        "entries": entries,
    }
    return _spec_digest(payload)


def _entry_spec_hashes(
    *,
    plan: SpawnPlan,
    entry: SpawnEntry,
    parent_anchor_hash: str = "",
) -> dict[str, Any]:
    """Hash independent entry dimensions so narrow edits preserve prior rolls."""

    randomization = plan.randomization if isinstance(plan.randomization, dict) else {}
    hashes: dict[str, Any] = {
        "version": 1,
        "roll": _spec_digest(randomization),
        "count": _spec_digest({
            "randomization": randomization,
            "count": entry.count,
        }),
        "source": _spec_digest({
            "randomization": randomization,
            "source": entry.source,
        }),
        "target": _spec_digest({
            "randomization": randomization,
            "target": entry.target,
            "parent_anchor": parent_anchor_hash,
        }),
        "traits": _spec_digest({
            "randomization": randomization,
            "traits": entry.traits,
        }),
        "placement": _spec_digest({
            "placement": entry.placement,
            "parent_anchor": parent_anchor_hash,
        }),
        "loot": _spec_digest(entry.loot),
        "conditions": _spec_digest({
            "zone_id": plan.zone_id,
            "conditions": entry.conditions,
        }),
    }
    hashes["anchor"] = _spec_digest({
        "source": hashes["source"],
        "target": hashes["target"],
        "placement": hashes["placement"],
    })
    hashes["materialization"] = _spec_digest({
        key: hashes[key]
        for key in (
            "source",
            "target",
            "traits",
            "placement",
            "loot",
            "conditions",
        )
    })
    return hashes


def _seed_for_plan(*, spawn_world: World, plan: SpawnPlan) -> str:
    seed_scope = str((plan.randomization or {}).get("seed_scope") or "instance").strip().lower()
    if seed_scope == "world":
        seed_basis = f"world:{plan.world_id}:{plan.slug}"
    elif seed_scope == "explicit":
        seed_basis = f"explicit:{plan.world_id}:{plan.slug}"
    else:
        seed_basis = f"instance:{spawn_world.id}:{plan.world_id}:{plan.slug}"
    return hashlib.sha256(seed_basis.encode("utf-8")).hexdigest()


def _rng_for_entry_dimension(
    *,
    run: SpawnPlanRun,
    entry: SpawnEntry,
    dimension: str,
    spec_hash: str,
    slot_key: str = "",
) -> random.Random:
    seed = f"{run.seed}:{entry.slug}:{dimension}:{slot_key}:{spec_hash}"
    return random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())


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
    if text.startswith("zone@"):
        raw_relative_id = text[len("zone@"):].strip()
        try:
            relative_id = int(raw_relative_id)
        except ValueError:
            raise serializers.ValidationError(f"{field_name} must use integer zone@<relative_id>.")
        zone = Zone.objects.filter(world=world, relative_id=relative_id).first()
    else:
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


def _source_lookup_world(*, world: World, model_cls: type) -> World:
    if world.instance_of_id and model_cls in INHERITED_INSTANCE_SOURCE_MODELS:
        return world.instance_of
    return world


def _source_resolution_scope(*, world: World, model_cls: type) -> str:
    if world.instance_of_id and model_cls in INHERITED_INSTANCE_SOURCE_MODELS:
        return "the inherited base world"
    return "this world"


def resolve_source(*, world: World, source_spec: Any, field_name: str = "source") -> ResolvedSource:
    source_ref = _source_ref_from_value(source_spec)
    prefix, ref_value = _parse_key_ref(source_ref)
    if prefix not in SOURCE_MODELS or not ref_value:
        raise serializers.ValidationError(
            f"{field_name} must use a supported ref such as mobdefinition.slug or itemdefinition.slug."
        )
    model_cls = SOURCE_MODELS[prefix]
    lookup_world = _source_lookup_world(world=world, model_cls=model_cls)
    queryset = model_cls.objects.filter(world=lookup_world)
    if ref_value.isdigit():
        source = queryset.filter(pk=int(ref_value)).first()
    else:
        source = queryset.filter(slug=ref_value).first()
    if source is None:
        scope = _source_resolution_scope(world=world, model_cls=model_cls)
        raise serializers.ValidationError(f"{field_name} does not resolve to authored content in {scope}.")
    source_slug = getattr(source, "slug", "") or str(source.pk)
    canonical_type = {
        ItemBundle: "itembundle",
        ItemDefinition: "itemdefinition",
        MobDefinition: "mobdefinition",
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
        entry_rooms = Room.objects.filter(pk=path.entry_room_id)
        if source_type.startswith("mob"):
            entry_rooms = entry_rooms.exclude(
                flags__code=adv_consts.ROOM_FLAG_NO_ROAM,
            )
        entry_room = entry_rooms.first()
        if entry_room is not None:
            return [entry_room]
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
    room_choice_cache: dict[
        tuple[int, str],
        tuple[list[Room], dict[str, Any]],
    ] | None = None,
) -> tuple[Room, dict[str, Any]]:
    cache_key = (entry.id, source_type)
    if room_choice_cache is not None and cache_key in room_choice_cache:
        rooms, state = room_choice_cache[cache_key]
        if state["target_type"] == "room":
            return rooms[0], dict(state)
        return rooms[rng.randrange(0, len(rooms))], dict(state)

    def remember(rooms: list[Room], state: dict[str, Any]) -> None:
        if room_choice_cache is not None:
            room_choice_cache[cache_key] = (rooms, state)

    target = _target_mapping(entry)
    if target.get("room"):
        room = _resolve_room(
            world=world,
            value=target.get("room"),
            field_name=f"entries.{entry.slug}.target.room",
        )
        state = {"target_type": "room", "target_id": room.id}
        remember([room], state)
        return room, dict(state)
    if target.get("room_ref"):
        room = _resolve_room(
            world=world,
            value=target.get("room_ref"),
            field_name=f"entries.{entry.slug}.target.room_ref",
        )
        state = {"target_type": "room", "target_id": room.id}
        remember([room], state)
        return room, dict(state)
    if target.get("zone"):
        zone = _resolve_zone(
            world=world,
            value=target.get("zone"),
            field_name=f"entries.{entry.slug}.target.zone",
        )
        rooms = _rooms_for_zone_target(zone, source_type=source_type)
        if not rooms:
            raise serializers.ValidationError(f"Spawn entry '{entry.slug}' zone target has no eligible rooms.")
        state = {"target_type": "zone", "target_id": zone.id}
        remember(rooms, state)
        room = rooms[rng.randrange(0, len(rooms))]
        return room, dict(state)
    if target.get("path"):
        path = _resolve_path(
            world=world,
            value=target.get("path"),
            field_name=f"entries.{entry.slug}.target.path",
        )
        rooms = _rooms_for_path_target(path, source_type=source_type)
        if not rooms:
            raise serializers.ValidationError(f"Spawn entry '{entry.slug}' path target has no eligible rooms.")
        state = {"target_type": "path", "target_id": path.id}
        remember(rooms, state)
        room = rooms[rng.randrange(0, len(rooms))]
        return room, dict(state)
    raise serializers.ValidationError(f"Spawn entry '{entry.slug}' must target a room, zone, path, or entry.")


def _target_entry_slug(entry: SpawnEntry) -> str:
    target = _target_mapping(entry)
    raw_target = target.get("entry") or target.get("parent_entry")
    return str(raw_target or "").strip()


def _entry_placement_mapping(entry: SpawnEntry) -> dict[str, Any]:
    if isinstance(entry.placement, dict):
        return entry.placement
    return {}


def _entry_cohort_spec(entry: SpawnEntry, *, parent: SpawnPlacement | None = None) -> dict[str, Any]:
    placement = _entry_placement_mapping(entry)
    raw_cohort = placement.get("cohort") or placement.get("cohort_slug")
    if isinstance(raw_cohort, dict):
        cohort_slug = str(
            raw_cohort.get("slug")
            or raw_cohort.get("name")
            or raw_cohort.get("id")
            or ""
        ).strip()
        raw_role = raw_cohort.get("role") or raw_cohort.get("cohort_role")
        raw_policy = raw_cohort.get("policy") or raw_cohort.get("cohort_policy")
    else:
        cohort_slug = str(raw_cohort or "").strip()
        raw_role = placement.get("cohort_role") or placement.get("role")
        raw_policy = placement.get("cohort_policy") or placement.get("policy")
    if not cohort_slug:
        return {}

    role = str(raw_role or ("follower" if parent is not None else "leader")).strip().lower()
    if role not in {"leader", "follower", "member"}:
        role = "member"
    policy = str(raw_policy or COHORT_RESPAWN_REFILL_MISSING).strip().lower()
    if policy not in COHORT_RESPAWN_POLICIES:
        policy = COHORT_RESPAWN_REFILL_MISSING
    return {
        "cohort_slug": cohort_slug,
        "cohort_role": role,
        "cohort_policy": policy,
    }


def _placement_roam_state(placement: SpawnPlacement) -> dict[str, Any]:
    state = placement.state if isinstance(placement.state, dict) else {}
    target_type = state.get("roam_target_type") or state.get("target_type")
    target_id = state.get("roam_target_id") or state.get("target_id")
    if target_type in {"zone", "path"} and target_id:
        return {
            "roam_target_type": target_type,
            "roam_target_id": target_id,
        }
    return {}


def _apply_cohort_state(
    *,
    entry: SpawnEntry,
    state: dict[str, Any],
    slot_index: int,
    parent: SpawnPlacement | None = None,
) -> dict[str, Any]:
    cohort = _entry_cohort_spec(entry, parent=parent)
    if not cohort:
        return state
    cohort_slot_index = parent.slot_index if parent is not None else slot_index
    state.update({
        **cohort,
        "cohort_slot_index": cohort_slot_index,
    })
    return state


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


def _placement_trait(trait: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(trait)
    payload.pop("weight", None)
    return payload


def _generate_traits(entry: SpawnEntry, rng: random.Random) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        traits = normalize_trait_table(
            entry.traits if isinstance(entry.traits, dict) else {},
            field_name=f"Spawn entry '{entry.slug}' traits",
        )
    except ValueError as exc:
        raise serializers.ValidationError(str(exc))
    selected = [
        _placement_trait(trait)
        for trait in traits.get("guaranteed") or []
    ]
    try:
        chance = int(traits.get("chance", 0) or 0)
    except (TypeError, ValueError):
        raise serializers.ValidationError(f"Spawn entry '{entry.slug}' traits.chance must be an integer.")
    pool = traits.get("pool") or []
    modifiers: dict[str, Any] = trait_modifiers(selected)
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
                    f"Spawn entry '{entry.slug}' trait pool weights must be integers."
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
                    selected_trait = _placement_trait(option)
                    selected.append(selected_trait)
                    modifiers.update(trait_modifiers([selected_trait]))
                    break
    return selected, modifiers


@dataclass(frozen=True)
class ActiveRunResolution:
    run: SpawnPlanRun | None
    needs_placement_sync: bool = False
    spec_changed: bool = False
    snapshot_stale: bool = False


@dataclass(frozen=True)
class PlacementSyncResult:
    active_count: int
    hot_placement_ids: frozenset[int]


def _active_entry_hashes(
    *,
    plan: SpawnPlan,
    entries: list[SpawnEntry],
) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    hashes_by_id: dict[int, dict[str, Any]] = {}
    hashes_by_slug: dict[str, dict[str, Any]] = {}
    for entry in (entry for entry in entries if entry.is_active):
        parent_slug = _target_entry_slug(entry)
        parent_hashes = hashes_by_slug.get(parent_slug, {})
        hashes = _entry_spec_hashes(
            plan=plan,
            entry=entry,
            parent_anchor_hash=str(parent_hashes.get("anchor") or ""),
        )
        hashes_by_id[entry.id] = hashes
        hashes_by_slug[entry.slug] = hashes
    return hashes_by_id, hashes_by_slug


def _entry_count_from_placements(
    *,
    entry: SpawnEntry,
    placements: list[SpawnPlacement],
) -> int | None:
    entry_placements = [
        placement
        for placement in placements
        if placement.entry_slug == entry.slug and not placement.is_retired
    ]
    parent_slug = _target_entry_slug(entry)
    if not parent_slug:
        return len(entry_placements)
    counts: dict[tuple[str, int], int] = {}
    for placement in entry_placements:
        if placement.parent_slot_index is None:
            continue
        key = (placement.parent_entry_slug, placement.parent_slot_index)
        counts[key] = counts.get(key, 0) + 1
    if counts:
        return max(counts.values())
    if any(
        placement.entry_slug == parent_slug and not placement.is_retired
        for placement in placements
    ):
        return 0
    return None


def _entry_states_match_current_spec(
    *,
    spec_hash: str,
    entry_states: Any,
) -> bool:
    return (
        isinstance(entry_states, dict)
        and entry_states.get("plan_spec_hash") == spec_hash
        and isinstance(entry_states.get("entries"), dict)
    )


def _baseline_entry_states_for_run(
    *,
    run: SpawnPlanRun,
    plan: SpawnPlan,
    entries: list[SpawnEntry],
) -> None:
    """Adopt current legacy placements when their run already matches the spec."""

    placements = list(run.placements.all())
    hashes_by_id, _ = _active_entry_hashes(plan=plan, entries=entries)
    entries_state = {}
    for entry in (entry for entry in entries if entry.is_active):
        entries_state[entry.slug] = {
            "hashes": hashes_by_id[entry.id],
            "count": _entry_count_from_placements(
                entry=entry,
                placements=placements,
            ),
        }
    run.entry_states = {
        "plan_spec_hash": run.spec_hash,
        "entries": entries_state,
    }
    run.save(update_fields=["entry_states", "modified_ts"])


def _active_run_for_plan(
    *,
    spawn_world: World,
    plan: SpawnPlan,
    entries: list[SpawnEntry],
    initial: bool = False,
) -> ActiveRunResolution:
    spec_hash = _plan_spec_hash(plan, entries=entries)
    run = SpawnPlanRun.objects.select_for_update().filter(
        spawn_world=spawn_world,
        plan=plan,
        status=SpawnPlanRun.STATUS_ACTIVE,
    ).order_by("-created_ts", "-id").first()
    if run is None:
        # Instance population is a start-of-run snapshot. Edits to a template
        # apply to future runs, not an in-progress completion cohort.
        if plan.world.instance_of_id and not initial:
            return ActiveRunResolution(run=None)
        run = SpawnPlanRun.objects.create(
            spawn_world=spawn_world,
            plan=plan,
            seed=_seed_for_plan(spawn_world=spawn_world, plan=plan),
            spec_hash=spec_hash,
        )
        return ActiveRunResolution(run=run, needs_placement_sync=True)
    if run.spec_hash != spec_hash and plan.world.instance_of_id and not initial:
        return ActiveRunResolution(run=run, snapshot_stale=True)
    if run.spec_hash != spec_hash:
        run.spec_hash = spec_hash
        run.generated_at = timezone.now()
        run.save(update_fields=["spec_hash", "generated_at", "modified_ts"])
        return ActiveRunResolution(
            run=run,
            needs_placement_sync=True,
            spec_changed=True,
        )
    if not _entry_states_match_current_spec(
        spec_hash=spec_hash,
        entry_states=run.entry_states,
    ):
        # Old workers can create current-spec runs with database-default state
        # during a rolling deployment. Adopt those placements without treating
        # them as an authored edit or refilling missing output early.
        _baseline_entry_states_for_run(
            run=run,
            plan=plan,
            entries=entries,
        )
    return ActiveRunResolution(run=run)


_TARGET_STATE_KEYS = {
    "target_type",
    "target_id",
    "roam_target_type",
    "roam_target_id",
}
_COHORT_STATE_KEYS = {
    "cohort_slug",
    "cohort_role",
    "cohort_policy",
    "cohort_slot_index",
}


def _source_family(source_type: str) -> str:
    return "mob" if source_type.startswith("mob") else "item"


def _merge_preserved_state(
    *,
    existing: dict[str, Any],
    desired: dict[str, Any],
    preserve_target: bool,
    preserve_cohort: bool,
) -> dict[str, Any]:
    if preserve_target and preserve_cohort:
        return copy.deepcopy(existing)
    merged = copy.deepcopy(desired)
    for preserve, keys in (
        (preserve_target, _TARGET_STATE_KEYS),
        (preserve_cohort, _COHORT_STATE_KEYS),
    ):
        if not preserve:
            continue
        for key in keys:
            if key in existing:
                merged[key] = copy.deepcopy(existing[key])
            else:
                merged.pop(key, None)
    return merged


def _sync_placements_for_run(
    *,
    run: SpawnPlanRun,
    entries: list[SpawnEntry],
    prune_retired: bool = False,
) -> PlacementSyncResult:
    """Diff deterministic desired slots into an active run without orphaning output."""

    plan = run.plan
    if prune_retired:
        from spawns.models import Item, Mob

        retired_ids = set(
            run.placements.filter(is_retired=True).values_list("id", flat=True)
        )
        if retired_ids:
            occupied_ids = {
                *Mob.objects.filter(
                    world_id=run.spawn_world_id,
                    spawn_placement_id__in=retired_ids,
                    is_pending_deletion=False,
                ).values_list("spawn_placement_id", flat=True),
                *Item.objects.filter(
                    world_id=run.spawn_world_id,
                    spawn_placement_id__in=retired_ids,
                    is_pending_deletion=False,
                ).values_list("spawn_placement_id", flat=True),
            }
            empty_retired_ids = retired_ids - occupied_ids
            if empty_retired_ids:
                run.placements.filter(pk__in=empty_retired_ids).delete()

    existing = list(
        run.placements.select_related("room").order_by(
            "entry_slug",
            "parent_entry_slug",
            "parent_slot_index",
            "slot_index",
            "id",
        )
    )
    root_placements: dict[tuple[str, int], SpawnPlacement] = {}
    nested_placements: dict[tuple[str, str, int, int], SpawnPlacement] = {}
    nested_ordinals: dict[tuple[str, str, int], int] = {}
    used_slots: dict[str, set[int]] = {}
    placements_by_entry: dict[str, list[SpawnPlacement]] = {}
    for placement in existing:
        placements_by_entry.setdefault(placement.entry_slug, []).append(placement)
        used_slots.setdefault(placement.entry_slug, set()).add(placement.slot_index)
        if placement.parent_entry_slug and placement.parent_slot_index is not None:
            group_key = (
                placement.entry_slug,
                placement.parent_entry_slug,
                placement.parent_slot_index,
            )
            ordinal = nested_ordinals.get(group_key, 0)
            nested_ordinals[group_key] = ordinal + 1
            nested_placements[(*group_key, ordinal)] = placement
        else:
            root_placements[(placement.entry_slug, placement.slot_index)] = placement

    run_entry_state = run.entry_states if isinstance(run.entry_states, dict) else {}
    old_entry_states = run_entry_state.get("entries", {})
    if not isinstance(old_entry_states, dict):
        old_entry_states = {}
    retained_entry_slugs = {placement.entry_slug for placement in existing}
    next_entry_states = {
        slug: copy.deepcopy(state)
        for slug, state in old_entry_states.items()
        if slug in retained_entry_slugs and isinstance(state, dict)
    }
    generated_by_entry: dict[str, list[SpawnPlacement]] = {}
    room_choice_cache: dict[
        tuple[int, str],
        tuple[list[Room], dict[str, Any]],
    ] = {}
    desired_ids: set[int] = set()
    hot_placement_ids: set[int] = set()
    changed_existing: dict[int, SpawnPlacement] = {}
    now = timezone.now()
    active_entries = [entry for entry in entries if entry.is_active]
    entry_hashes, _ = _active_entry_hashes(plan=plan, entries=entries)
    next_slots = {
        entry_slug: max(slots, default=-1) + 1
        for entry_slug, slots in used_slots.items()
    }

    def reserve_slot(*, entry_slug: str, preferred: int) -> int:
        slots = used_slots.setdefault(entry_slug, set())
        if preferred not in slots:
            slots.add(preferred)
            next_slots[entry_slug] = max(
                next_slots.get(entry_slug, 0),
                preferred + 1,
            )
            return preferred
        slot_index = next_slots.get(entry_slug, max(slots, default=-1) + 1)
        while slot_index in slots:
            slot_index += 1
        slots.add(slot_index)
        next_slots[entry_slug] = slot_index + 1
        return slot_index

    def upsert_placement(
        *,
        entry: SpawnEntry,
        proposed_slot_index: int,
        room: Room,
        source: ResolvedSource,
        traits: list[dict[str, Any]],
        modifiers: dict[str, Any],
        state: dict[str, Any],
        old_hashes: dict[str, Any],
        parent: SpawnPlacement | None = None,
        nested_index: int | None = None,
    ) -> SpawnPlacement:
        hashes = entry_hashes[entry.id]
        if parent is None:
            placement = root_placements.get((entry.slug, proposed_slot_index))
        else:
            placement = nested_placements.get((
                entry.slug,
                parent.entry_slug,
                parent.slot_index,
                int(nested_index or 0),
            ))

        if placement is None:
            slot_index = reserve_slot(
                entry_slug=entry.slug,
                preferred=proposed_slot_index,
            )
            placement = SpawnPlacement.objects.create(
                run=run,
                entry_slug=entry.slug,
                slot_index=slot_index,
                room=room,
                source_type=source.source_type,
                source_slug=source.source_slug,
                source_id=source.source_id,
                parent_entry_slug=parent.entry_slug if parent is not None else "",
                parent_slot_index=parent.slot_index if parent is not None else None,
                traits=traits,
                modifiers=modifiers,
                state=state,
                is_retired=False,
            )
            hot_placement_ids.add(placement.id)
            desired_ids.add(placement.id)
            return placement

        was_retired = placement.is_retired
        preserve_source = (
            not was_retired
            and old_hashes.get("source") == hashes["source"]
        )
        preserve_target = (
            not was_retired
            and old_hashes.get("target") == hashes["target"]
            and _source_family(placement.source_type) == _source_family(source.source_type)
        )
        preserve_traits = (
            not was_retired
            and old_hashes.get("traits") == hashes["traits"]
        )
        preserve_cohort = (
            not was_retired
            and old_hashes.get("placement") == hashes["placement"]
        )
        existing_state = placement.state if isinstance(placement.state, dict) else {}
        desired_values = {
            "room_id": placement.room_id if preserve_target else room.id,
            "source_type": placement.source_type if preserve_source else source.source_type,
            "source_slug": placement.source_slug if preserve_source else source.source_slug,
            "source_id": placement.source_id if preserve_source else source.source_id,
            "parent_entry_slug": parent.entry_slug if parent is not None else "",
            "parent_slot_index": parent.slot_index if parent is not None else None,
            "traits": placement.traits if preserve_traits else traits,
            "modifiers": placement.modifiers if preserve_traits else modifiers,
            "state": _merge_preserved_state(
                existing=existing_state,
                desired=state,
                preserve_target=preserve_target,
                preserve_cohort=preserve_cohort,
            ),
            "is_retired": False,
        }
        changed = any(
            getattr(placement, field_name) != value
            for field_name, value in desired_values.items()
        )
        for field_name, value in desired_values.items():
            setattr(placement, field_name, value)
        if changed:
            placement.modified_ts = now
            changed_existing[placement.id] = placement
        if (
            was_retired
            or old_hashes.get("materialization") != hashes["materialization"]
        ):
            hot_placement_ids.add(placement.id)
        desired_ids.add(placement.id)
        return placement

    for entry in active_entries:
        hashes = entry_hashes[entry.id]
        old_entry_state = old_entry_states.get(entry.slug, {})
        if not isinstance(old_entry_state, dict):
            old_entry_state = {}
        old_hashes = old_entry_state.get("hashes", {})
        if not isinstance(old_hashes, dict):
            old_hashes = {}
        stored_count = old_entry_state.get("count")
        if (
            old_hashes.get("count") == hashes["count"]
            and isinstance(stored_count, int)
            and not isinstance(stored_count, bool)
        ):
            count = max(0, stored_count)
        else:
            count = _entry_count(
                entry,
                _rng_for_entry_dimension(
                    run=run,
                    entry=entry,
                    dimension="count",
                    spec_hash=hashes["count"],
                ),
            )

        active_existing = [
            placement
            for placement in placements_by_entry.get(entry.slug, [])
            if not placement.is_retired
        ]
        parent_entry_slug = _target_entry_slug(entry)
        parents = generated_by_entry.get(parent_entry_slug, []) if parent_entry_slug else []
        layout_matches = False
        if old_hashes == hashes:
            if parent_entry_slug:
                actual_counts: dict[tuple[str, int], int] = {}
                for placement in active_existing:
                    if placement.parent_slot_index is None:
                        break
                    key = (placement.parent_entry_slug, placement.parent_slot_index)
                    actual_counts[key] = actual_counts.get(key, 0) + 1
                else:
                    expected_counts = {
                        (parent.entry_slug, parent.slot_index): count
                        for parent in parents
                        if count > 0
                    }
                    layout_matches = actual_counts == expected_counts
            else:
                layout_matches = (
                    len(active_existing) == count
                    and all(not placement.parent_entry_slug for placement in active_existing)
                )
        if layout_matches:
            desired_ids.update(placement.id for placement in active_existing)
            generated_by_entry[entry.slug] = active_existing
            next_entry_states[entry.slug] = {
                "hashes": hashes,
                "count": count,
            }
            continue

        created: list[SpawnPlacement] = []
        if parent_entry_slug:
            for parent in parents:
                for nested_index in range(count):
                    slot_key = (
                        f"parent:{parent.entry_slug}:"
                        f"{parent.slot_index}:{nested_index}"
                    )
                    source_spec = _choose_source_spec(
                        entry,
                        _rng_for_entry_dimension(
                            run=run,
                            entry=entry,
                            dimension="source",
                            spec_hash=hashes["source"],
                            slot_key=slot_key,
                        ),
                    )
                    source = resolve_source(
                        world=plan.world,
                        source_spec=source_spec,
                        field_name=f"entries.{entry.slug}.source",
                    )
                    traits, modifiers = _generate_traits(
                        entry,
                        _rng_for_entry_dimension(
                            run=run,
                            entry=entry,
                            dimension="traits",
                            spec_hash=hashes["traits"],
                            slot_key=slot_key,
                        ),
                    )
                    state = _apply_cohort_state(
                        entry=entry,
                        parent=parent,
                        slot_index=len(created),
                        state={
                            "target_type": "entry",
                            "target_id": parent.id,
                            **_placement_roam_state(parent),
                        },
                    )
                    created.append(upsert_placement(
                        entry=entry,
                        proposed_slot_index=len(created),
                        room=parent.room,
                        source=source,
                        traits=traits,
                        modifiers=modifiers,
                        state=state,
                        old_hashes=old_hashes,
                        parent=parent,
                        nested_index=nested_index,
                    ))
        else:
            for slot_index in range(count):
                slot_key = f"root:{slot_index}"
                source_spec = _choose_source_spec(
                    entry,
                    _rng_for_entry_dimension(
                        run=run,
                        entry=entry,
                        dimension="source",
                        spec_hash=hashes["source"],
                        slot_key=slot_key,
                    ),
                )
                source = resolve_source(
                    world=plan.world,
                    source_spec=source_spec,
                    field_name=f"entries.{entry.slug}.source",
                )
                room, state = _choose_room_for_entry(
                    world=plan.world,
                    entry=entry,
                    source_type=source.source_type,
                    rng=_rng_for_entry_dimension(
                        run=run,
                        entry=entry,
                        dimension="target",
                        spec_hash=hashes["target"],
                        slot_key=slot_key,
                    ),
                    room_choice_cache=room_choice_cache,
                )
                traits, modifiers = _generate_traits(
                    entry,
                    _rng_for_entry_dimension(
                        run=run,
                        entry=entry,
                        dimension="traits",
                        spec_hash=hashes["traits"],
                        slot_key=slot_key,
                    ),
                )
                created.append(upsert_placement(
                    entry=entry,
                    proposed_slot_index=slot_index,
                    room=room,
                    source=source,
                    traits=traits,
                    modifiers=modifiers,
                    state=_apply_cohort_state(
                        entry=entry,
                        state=state,
                        slot_index=slot_index,
                    ),
                    old_hashes=old_hashes,
                ))
        generated_by_entry[entry.slug] = created
        next_entry_states[entry.slug] = {
            "hashes": hashes,
            "count": count,
        }

    for placement in existing:
        if placement.id in desired_ids or placement.is_retired:
            continue
        placement.is_retired = True
        placement.modified_ts = now
        changed_existing[placement.id] = placement

    if changed_existing:
        SpawnPlacement.objects.bulk_update(
            changed_existing.values(),
            [
                "room",
                "source_type",
                "source_slug",
                "source_id",
                "parent_entry_slug",
                "parent_slot_index",
                "traits",
                "modifiers",
                "state",
                "is_retired",
                "modified_ts",
            ],
            batch_size=500,
        )
    next_run_entry_state = {
        "plan_spec_hash": run.spec_hash,
        "entries": next_entry_states,
    }
    if run.entry_states != next_run_entry_state:
        run.entry_states = next_run_entry_state
        run.save(update_fields=["entry_states", "modified_ts"])

    return PlacementSyncResult(
        active_count=len(desired_ids),
        hot_placement_ids=frozenset(hot_placement_ids),
    )


def _sync_live_mob_groups(
    *,
    spawn_world: World,
    placement_ids: frozenset[int],
) -> None:
    if not placement_ids:
        return
    from spawns.models import Mob

    changed = []
    mobs = Mob.objects.filter(
        world=spawn_world,
        spawn_placement_id__in=placement_ids,
        is_pending_deletion=False,
    ).select_related("spawn_placement")
    now = timezone.now()
    for mob in mobs:
        expected_group_id = _placement_group_id(mob.spawn_placement) or None
        if mob.group_id == expected_group_id:
            continue
        mob.group_id = expected_group_id
        mob.modified_ts = now
        changed.append(mob)
    if changed:
        Mob.objects.bulk_update(
            changed,
            ["group_id", "modified_ts"],
            batch_size=500,
        )


def _placement_source(placement: SpawnPlacement):
    model_cls = SOURCE_MODELS.get(placement.source_type)
    if model_cls is None:
        return None
    return model_cls.objects.filter(pk=placement.source_id).first()


def _rng_for_placement(placement: SpawnPlacement) -> random.Random:
    run_entry_state = (
        placement.run.entry_states
        if isinstance(placement.run.entry_states, dict)
        else {}
    )
    entry_states = run_entry_state.get("entries", {})
    if not isinstance(entry_states, dict):
        entry_states = {}
    entry_state = entry_states.get(placement.entry_slug, {})
    hashes = entry_state.get("hashes", {}) if isinstance(entry_state, dict) else {}
    roll_hash = str(hashes.get("roll") or "") if isinstance(hashes, dict) else ""
    seed = (
        f"{placement.run.seed}:"
        f"{roll_hash}:"
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
        "traits": list(placement.traits or []),
        "trait_keys": trait_keys(list(placement.traits or [])),
        "modifiers": dict(placement.modifiers or {}),
    }
    cohort = _placement_cohort_state(placement)
    if cohort:
        metadata["spawn_plan"]["cohort"] = cohort
    entity.roll_metadata = metadata


def _apply_spawn_modifiers(entity: Any, placement: SpawnPlacement) -> list[str]:
    modifiers = placement.modifiers if isinstance(placement.modifiers, dict) else {}
    return apply_numeric_modifiers(entity, modifiers)


def _placement_roams(placement: SpawnPlacement):
    state = placement.state if isinstance(placement.state, dict) else {}
    target_type = state.get("roam_target_type") or state.get("target_type")
    target_id = state.get("roam_target_id") or state.get("target_id")
    if target_type == "zone":
        return Zone.objects.filter(pk=target_id).first()
    if target_type == "path":
        return Path.objects.filter(pk=target_id).first()
    return None


def _placement_cohort_state(placement: SpawnPlacement) -> dict[str, Any]:
    state = placement.state if isinstance(placement.state, dict) else {}
    cohort_slug = str(state.get("cohort_slug") or "").strip()
    if not cohort_slug:
        return {}
    try:
        cohort_slot_index = int(state.get("cohort_slot_index", placement.slot_index))
    except (TypeError, ValueError):
        cohort_slot_index = placement.slot_index
    return {
        "slug": cohort_slug,
        "role": str(state.get("cohort_role") or "member").strip().lower(),
        "policy": str(state.get("cohort_policy") or COHORT_RESPAWN_REFILL_MISSING).strip().lower(),
        "slot_index": cohort_slot_index,
    }


def _placement_group_id(placement: SpawnPlacement) -> str:
    cohort = _placement_cohort_state(placement)
    if not cohort:
        return ""
    return (
        f"spawnplan.{placement.run_id}."
        f"{cohort['slug']}."
        f"{cohort['slot_index']}"
    )


def _roam_target_allows_room(roams, room: Room) -> bool:
    if isinstance(roams, Zone):
        return room.zone_id == roams.id
    if isinstance(roams, Path):
        return roams.rooms.filter(pk=room.id).exists()
    return True


def _live_cohort_members(*, placement: SpawnPlacement, spawn_world: World) -> list:
    group_id = _placement_group_id(placement)
    if not group_id:
        return []
    from spawns.models import Mob

    return list(
        Mob.objects.filter(
            world=spawn_world,
            group_id=group_id,
            is_pending_deletion=False,
            room_id__isnull=False,
        )
        .select_related("room", "spawn_placement")
        .order_by("id")
    )


def _preferred_cohort_anchor(members: list):
    for member in members:
        placement = getattr(member, "spawn_placement", None)
        state = placement.state if placement and isinstance(placement.state, dict) else {}
        if str(state.get("cohort_role") or "").strip().lower() == "leader":
            return member
    return members[0] if members else None


def _placement_spawn_room(
    *,
    placement: SpawnPlacement,
    spawn_world: World,
    roams: Any,
) -> Room:
    cohort = _placement_cohort_state(placement)
    if not cohort or cohort["policy"] != COHORT_RESPAWN_REFILL_MISSING:
        return placement.room
    anchor = _preferred_cohort_anchor(
        _live_cohort_members(placement=placement, spawn_world=spawn_world)
    )
    if anchor is None or anchor.room_id is None:
        return placement.room
    if roams is not None and not _roam_target_allows_room(roams, anchor.room):
        return placement.room
    return anchor.room


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


def _placement_has_live_output(
    *,
    placement: SpawnPlacement,
    reconcile_context: SpawnReconcileContext,
) -> bool:
    # Check both output families. A live mob still satisfies its logical slot
    # after a mob-to-item edit (and vice versa) until that old output leaves.
    return reconcile_context.placement_has_live_output(placement.id)


def _materialize_placement(
    *,
    placement: SpawnPlacement,
    spawn_world: World,
    reconcile_context: SpawnReconcileContext,
):
    if _placement_has_live_output(
        placement=placement,
        reconcile_context=reconcile_context,
    ):
        return []
    target = placement.room
    if placement.parent_entry_slug:
        parent_target = _parent_instance(placement=placement, spawn_world=spawn_world)
        if parent_target is None and not (
            placement.source_type.startswith("mob")
            and _live_cohort_members(placement=placement, spawn_world=spawn_world)
        ):
            return []
        target = parent_target or placement.room
    else:
        target = placement.room
    if placement.source_type.startswith("mob"):
        roams = _placement_roams(placement)
        spawn_room = _placement_spawn_room(
            placement=placement,
            spawn_world=spawn_world,
            roams=roams,
        )
        if (
            _placement_roam_state(placement)
            and reconcile_context.room_is_no_roam(spawn_room.id)
        ):
            return []
    source = _placement_source(placement)
    if source is None:
        return []
    entry = placement.run.plan.entries.filter(slug=placement.entry_slug).first()
    if placement.source_type.startswith("mob"):
        spawn_kwargs = {
            "target": spawn_room,
            "spawn_world": spawn_world,
            "roams": roams,
            "rule": None,
        }
        if placement.source_type == "mobdefinition":
            spawn_kwargs["rng"] = _rng_for_placement(placement)
        spawned = source.spawn(**spawn_kwargs)
        spawned.spawn_placement = placement
        group_id = _placement_group_id(placement)
        if group_id:
            spawned.group_id = group_id
        if entry is not None:
            spawned.loot = merge_loot_tables(
                spawned.loot if isinstance(spawned.loot, dict) else {},
                entry.loot if isinstance(entry.loot, dict) else {},
            )
        _apply_spawn_origin_metadata(spawned, placement)
        placement_trait_instances = trait_instances(
            list(placement.traits or []),
            source="spawn_plan",
            source_ref=f"spawnplan.{placement.run.plan.slug}/{placement.entry_slug}/{placement.slot_index}",
        )
        spawned.trait_instances = [
            *(spawned.trait_instances or []),
            *placement_trait_instances,
        ]
        modifier_fields = _apply_spawn_modifiers(spawned, placement)
        update_fields = [
            "spawn_placement",
            "roll_metadata",
            "loot",
            "trait_instances",
            *modifier_fields,
            "modified_ts",
        ]
        if group_id:
            update_fields.append("group_id")
        spawned.save(update_fields=update_fields)
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


def reconcile_spawn_plan(
    *,
    spawn_world: World,
    plan: SpawnPlan,
    initial: bool = False,
    repopulate: bool = False,
    reconcile_context: SpawnReconcileContext | None = None,
) -> dict[str, Any]:
    if not spawn_world.context:
        raise TypeError("Can only run spawn plans on spawn worlds.")
    with transaction.atomic():
        plan = SpawnPlan.objects.select_for_update().select_related("zone", "world").get(pk=plan.pk)
        if not plan.is_active:
            return {"plan": plan.slug, "placements": 0, "spawned": 0, "skipped": True}
        if not _plan_conditions_pass(spawn_world=spawn_world, plan=plan):
            return {"plan": plan.slug, "placements": 0, "spawned": 0, "skipped": True}
        entries = list(plan.entries.all().order_by("order", "created_ts", "id"))
        resolution = _active_run_for_plan(
            spawn_world=spawn_world,
            plan=plan,
            entries=entries,
            initial=initial,
        )
        run = resolution.run
        if run is None:
            return {"plan": plan.slug, "placements": 0, "spawned": 0, "skipped": True}
        if resolution.snapshot_stale:
            return {
                "plan": plan.slug,
                "placements": run.placements.filter(is_retired=False).count(),
                "spawned": 0,
                "skipped": True,
            }

        sync_result = None
        if resolution.needs_placement_sync:
            sync_result = _sync_placements_for_run(
                run=run,
                entries=entries,
                prune_retired=resolution.spec_changed,
            )
            _sync_live_mob_groups(
                spawn_world=spawn_world,
                placement_ids=sync_result.hot_placement_ids,
            )

        is_due = _plan_is_due(run=run, initial=initial, repopulate=repopulate)
        hot_placement_ids = (
            sync_result.hot_placement_ids
            if sync_result is not None
            else frozenset()
        )
        active_placements = run.placements.filter(is_retired=False)
        active_count = (
            sync_result.active_count
            if sync_result is not None
            else active_placements.count()
        )
        if not is_due and not hot_placement_ids:
            return {
                "plan": plan.slug,
                "placements": active_count,
                "spawned": 0,
                "skipped": True,
            }

        reconcile_context = reconcile_context or SpawnReconcileContext(
            authored_world_id=plan.world_id,
            spawn_world_id=spawn_world.id,
        )
        entries_by_slug = {
            entry.slug: entry
            for entry in entries
            if entry.is_active
        }
        placements = active_placements.select_related("room", "run__plan").order_by("id")
        if not is_due:
            # A builder edit applies its changed logical slots immediately but
            # does not refill unrelated missing slots before their deadline.
            placements = placements.filter(pk__in=hot_placement_ids)
        spawned_count = 0
        for placement in placements:
            entry = entries_by_slug.get(placement.entry_slug)
            if entry is None:
                continue
            if not _entry_conditions_pass(spawn_world=spawn_world, plan=plan, entry=entry):
                continue
            spawned_count += len(_materialize_placement(
                placement=placement,
                spawn_world=spawn_world,
                reconcile_context=reconcile_context,
            ))
        if is_due:
            run.last_reconciled_at = timezone.now()
            run.save(update_fields=["last_reconciled_at", "modified_ts"])
        return {
            "plan": plan.slug,
            "placements": active_count,
            "spawned": spawned_count,
            "skipped": False,
        }


def run_spawn_plans(
    *,
    world: World,
    zone_id: int | None = None,
    initial: bool = False,
    repopulate: bool = False,
    reconcile_context: SpawnReconcileContext | None = None,
) -> list[dict[str, Any]]:
    if not world.context:
        raise TypeError("Can only run spawn plans on spawn worlds.")
    reconcile_context = reconcile_context or SpawnReconcileContext(
        authored_world_id=world.context_id,
        spawn_world_id=world.id,
    )
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
                reconcile_context=reconcile_context,
            )
        )
    return results
