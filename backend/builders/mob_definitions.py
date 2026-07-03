from __future__ import annotations

from typing import Any

from django.db.models import NOT_PROVIDED
from django.db.models import Q

from builders.models import (
    FACTION_ASSIGNMENT_SOURCE_MOB_DEFINITION,
    FACTION_TYPE_CORE,
)
from builders.item_definitions import (
    RollResult,
    normalize_attribute_map,
    roll_item_randomization,
)
from core.mob_traits import (
    apply_numeric_modifiers,
    modifiers_from_trait_instances,
    trait_instances,
)
from core.stat_system import DIRECT_STAT_KEYS, compute_attribute_formula_stats
from core.model_mixins import CharMixin, MobMixin
from config import constants as adv_consts


def _field_names(model_cls) -> set[str]:
    return {field.name for field in model_cls._meta.fields if field.name != "id"}


CHAR_FIELD_NAMES = _field_names(CharMixin)
MOB_FIELD_NAMES = _field_names(MobMixin)
MOB_BASE_FIELD_NAMES = CHAR_FIELD_NAMES | MOB_FIELD_NAMES


def mob_definition_property_fields() -> tuple[str, ...]:
    excluded = {
        "name",
        "description",
        "room_description",
        "keywords",
        "attributes",
        "type",
        "health",
        "energy",
        "stamina",
        "group_id",
        "traits",
    }
    return tuple(sorted(MOB_BASE_FIELD_NAMES - excluded))


def _default_for_field(field) -> Any:
    if field.default is not NOT_PROVIDED:
        return field.get_default()
    if field.null:
        return None
    if getattr(field, "empty_strings_allowed", False):
        return ""
    return None


def _template_field_defaults() -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for model_cls in (CharMixin, MobMixin):
        for field in model_cls._meta.fields:
            if field.name == "id":
                continue
            fields[field.name] = _default_for_field(field)
    return fields


def _normalize_for_mob_model(fields: dict[str, Any]) -> dict[str, Any]:
    from spawns.models import Mob

    normalized = dict(fields)
    for field_name, value in list(normalized.items()):
        mob_field = Mob._meta.get_field(field_name)
        if value is None and not mob_field.null:
            if mob_field.empty_strings_allowed:
                normalized[field_name] = ""
            elif mob_field.has_default():
                normalized[field_name] = mob_field.get_default()
    return normalized


def _merge_attributes(*maps: dict[str, Any]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for values in maps:
        normalized = normalize_attribute_map(values or {})
        for key, value in normalized.items():
            merged[key] = merged.get(key, 0.0) + value
    return merged


def roll_mob_randomization(
    definition,
    world_stat_system: dict[str, Any],
    rng=None,
) -> RollResult:
    return roll_item_randomization(definition, world_stat_system, rng=rng)


def _mob_fields_from_definition(definition, attributes: dict[str, float]) -> dict[str, Any]:
    fields = _template_field_defaults()
    fields["name"] = definition.name or fields.get("name") or "Unnamed Mob"
    fields["description"] = definition.description or ""
    fields["room_description"] = definition.room_description or None
    fields["keywords"] = definition.keywords or None
    fields["type"] = definition.mob_type or fields.get("type")

    for key, value in (definition.base_properties or {}).items():
        if key not in MOB_BASE_FIELD_NAMES or key in {
            "attributes",
            "name",
            "health",
            "energy",
            "stamina",
            "group_id",
            "traits",
        }:
            continue
        if key == "aggression":
            value = adv_consts.canonical_mob_aggression(value)
        fields[key] = value

    fields["attributes"] = attributes
    fields = _normalize_for_mob_model(fields)
    formula_stats = compute_attribute_formula_stats(
        world=definition.world,
        attributes=fields.get("attributes") or {},
        archetype=fields.get("archetype"),
    )
    for stat_key in DIRECT_STAT_KEYS:
        bonus = formula_stats.get(stat_key)
        if not bonus:
            continue
        fields[stat_key] = int(fields.get(stat_key) or 0) + int(bonus)
    return fields


def _runtime_group_id(definition, rule):
    if rule and rule.loader.is_group:
        return rule.loader.key
    if definition.assists:
        return definition.key
    return None


def _copy_definition_faction_assignments(mob, definition) -> None:
    mob.faction_assignments.filter(
        source=FACTION_ASSIGNMENT_SOURCE_MOB_DEFINITION,
    ).delete()

    definition_assignments = list(
        definition.faction_assignments.select_related("faction").all()
    )
    for assignment in definition_assignments:
        faction = assignment.faction
        if not faction:
            continue
        is_core = faction.type == FACTION_TYPE_CORE or faction.is_core
        if is_core:
            has_existing_core = (
                mob.faction_assignments
                .filter(Q(faction__type=FACTION_TYPE_CORE) | Q(faction__is_core=True))
                .exists()
            )
            if has_existing_core:
                continue
        elif mob.faction_assignments.filter(faction=faction).exists():
            continue

        mob.faction_assignments.create(
            faction=faction,
            value=assignment.value,
            source=FACTION_ASSIGNMENT_SOURCE_MOB_DEFINITION,
        )


def spawn_mob_from_definition(
    definition,
    target,
    spawn_world,
    *,
    rng=None,
    roams=None,
    rule=None,
):
    from core.stat_system import get_world_stat_system
    from spawns.models import Mob

    roll_result = roll_mob_randomization(
        definition,
        get_world_stat_system(definition.world),
        rng=rng,
    )
    attributes = _merge_attributes(
        definition.attributes or {},
        roll_result.attributes,
    )
    mob_fields = _mob_fields_from_definition(definition, attributes)
    for field_name in ("health", "energy", "stamina", "group_id"):
        mob_fields.pop(field_name, None)

    roll_metadata = {
        "source_definition_slug": definition.slug,
        "randomization_version": roll_result.randomization_version,
        "rolled_at_definition_modified_ts": definition.modified_ts.isoformat()
        if definition.modified_ts else "",
        "ignored_attributes": roll_result.ignored_attributes,
        "randomized": roll_result.randomized,
    }

    mob = Mob.objects.create(
        world=spawn_world,
        room=target,
        definition=definition,
        definition_slug_snapshot=definition.slug,
        roll_metadata=roll_metadata,
        attackable=definition.attackable,
        health=mob_fields.get("health_max") or 1,
        stamina=mob_fields.get("stamina_max") or 0,
        energy=mob_fields.get("energy_max") or 0,
        group_id=_runtime_group_id(definition, rule),
        roams=roams,
        rule=rule,
        **mob_fields,
    )
    definition_trait_instances = trait_instances(
        definition.traits or [],
        source="mob_definition",
        source_ref=f"mobdefinition.{definition.slug}",
    )
    trait_update_fields = apply_numeric_modifiers(
        mob,
        modifiers_from_trait_instances(definition_trait_instances),
    )
    mob.trait_instances = definition_trait_instances
    mob.save(update_fields=list(dict.fromkeys([
        "trait_instances",
        *trait_update_fields,
        "modified_ts",
    ])))
    mob.create_corpse()
    _copy_definition_faction_assignments(mob, definition)
    if definition.merchant_profile_id:
        from spawns.merchants import create_or_update_merchant_runtime

        create_or_update_merchant_runtime(mob)
    return mob


def sync_spawned_mobs_from_definition(definition) -> int:
    from spawns.models import Mob

    updated = 0
    timestamp = (
        definition.modified_ts.isoformat()
        if definition.modified_ts else ""
    )
    queryset = Mob.objects.filter(
        definition=definition,
        is_pending_deletion=False,
    )
    for mob in queryset.iterator(chunk_size=200):
        roll_metadata = mob.roll_metadata if isinstance(mob.roll_metadata, dict) else {}
        if roll_metadata.get("randomized"):
            attributes = mob.attributes or {}
        else:
            attributes = _merge_attributes(
                definition.attributes or {},
            )
        mob_fields = _mob_fields_from_definition(definition, attributes)
        for field_name in ("health", "energy", "stamina", "group_id"):
            mob_fields.pop(field_name, None)
        for field_name, value in mob_fields.items():
            setattr(mob, field_name, value)
        mob.attackable = definition.attackable
        mob.health = mob.health_max
        mob.stamina = mob.stamina_max
        mob.energy = mob.energy_max
        existing_trait_instances = [
            instance
            for instance in (mob.trait_instances or [])
            if (instance or {}).get("source") != "mob_definition"
        ]
        definition_trait_instances = trait_instances(
            definition.traits or [],
            source="mob_definition",
            source_ref=f"mobdefinition.{definition.slug}",
        )
        mob.trait_instances = [
            *definition_trait_instances,
            *existing_trait_instances,
        ]
        trait_update_fields = apply_numeric_modifiers(
            mob,
            modifiers_from_trait_instances(mob.trait_instances),
        )
        mob.roll_metadata = {
            **roll_metadata,
            "source_definition_slug": definition.slug,
            "rolled_at_definition_modified_ts": timestamp,
        }
        mob.definition_slug_snapshot = definition.slug
        mob.save(
            update_fields=list(dict.fromkeys([
                *mob_fields.keys(),
                "health",
                "stamina",
                "energy",
                "attackable",
                "trait_instances",
                *trait_update_fields,
                "roll_metadata",
                "definition_slug_snapshot",
                "modified_ts",
            ]))
        )
        _copy_definition_faction_assignments(mob, definition)
        from spawns.merchants import create_or_update_merchant_runtime

        create_or_update_merchant_runtime(mob)
        updated += 1
    return updated
