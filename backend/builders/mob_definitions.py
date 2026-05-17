from __future__ import annotations

from typing import Any

from django.db.models import NOT_PROVIDED

from builders.item_definitions import (
    RollResult,
    normalize_input_attribute_map,
    roll_item_randomization,
)
from core.model_mixins import CharMixin, MobMixin


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
        "input_attributes",
        "type",
        "health",
        "energy",
        "stamina",
        "group_id",
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


def _merge_input_attributes(*maps: dict[str, Any]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for values in maps:
        normalized = normalize_input_attribute_map(values or {})
        for key, value in normalized.items():
            merged[key] = merged.get(key, 0.0) + value
    return merged


def roll_mob_randomization(
    definition,
    world_stat_system: dict[str, Any],
    rng=None,
) -> RollResult:
    return roll_item_randomization(definition, world_stat_system, rng=rng)


def _mob_fields_from_definition(definition, input_attributes: dict[str, float]) -> dict[str, Any]:
    fields = _template_field_defaults()
    fields["name"] = definition.name or fields.get("name") or "Unnamed Mob"
    fields["description"] = definition.description or ""
    fields["room_description"] = definition.room_description or None
    fields["keywords"] = definition.keywords or None
    fields["type"] = definition.mob_type or fields.get("type")

    for key, value in (definition.base_properties or {}).items():
        if key not in MOB_BASE_FIELD_NAMES or key in {
            "input_attributes",
            "name",
            "health",
            "energy",
            "stamina",
            "group_id",
        }:
            continue
        fields[key] = value

    fields["input_attributes"] = input_attributes
    return _normalize_for_mob_model(fields)


def _runtime_group_id(definition, rule):
    if rule and rule.loader.is_group:
        return rule.loader.key
    if definition.assists:
        return definition.key
    return None


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
    input_attributes = _merge_input_attributes(
        definition.base_input_attributes or {},
        roll_result.input_attributes,
    )
    mob_fields = _mob_fields_from_definition(definition, input_attributes)
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
        health=mob_fields.get("health_max") or 1,
        stamina=mob_fields.get("stamina_max") or 0,
        energy=mob_fields.get("energy_max") or 0,
        group_id=_runtime_group_id(definition, rule),
        roams=roams,
        rule=rule,
        **mob_fields,
    )
    mob.create_corpse()
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
            input_attributes = mob.input_attributes or {}
        else:
            input_attributes = _merge_input_attributes(
                definition.base_input_attributes or {},
            )
        mob_fields = _mob_fields_from_definition(definition, input_attributes)
        for field_name in ("health", "energy", "stamina", "group_id"):
            mob_fields.pop(field_name, None)
        for field_name, value in mob_fields.items():
            setattr(mob, field_name, value)
        mob.health = mob.health_max
        mob.stamina = mob.stamina_max
        mob.energy = mob.energy_max
        mob.roll_metadata = {
            **roll_metadata,
            "source_definition_slug": definition.slug,
            "rolled_at_definition_modified_ts": timestamp,
        }
        mob.definition_slug_snapshot = definition.slug
        mob.save(
            update_fields=[
                *mob_fields.keys(),
                "health",
                "stamina",
                "energy",
                "roll_metadata",
                "definition_slug_snapshot",
                "modified_ts",
            ]
        )
        updated += 1
    return updated
