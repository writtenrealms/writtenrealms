from __future__ import annotations

import copy
import hashlib
import random
from typing import Any

from django.utils.text import slugify
from rest_framework import serializers

from builders.models import ItemBundle, ItemDefinition
from core.condition_dsl import ConditionContext, evaluate_condition, validate_condition_payload


LOOT_SOURCE_MODELS = {
    "itembundle": ItemBundle,
    "item_bundle": ItemBundle,
    "itemdefinition": ItemDefinition,
    "item_definition": ItemDefinition,
}


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise serializers.ValidationError(f"{field_name} must be true or false.")


def _coerce_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{field_name} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise serializers.ValidationError(f"{field_name} must be an integer.")


def _slug_or_default(value: Any, *, fallback: str, field_name: str) -> str:
    slug = slugify(str(value or "").strip()) if value not in (None, "") else fallback
    if not slug:
        raise serializers.ValidationError(f"{field_name} must be a non-empty slug.")
    return slug


def _parse_source_ref(value: Any, *, field_name: str) -> tuple[str, str]:
    if isinstance(value, dict):
        value = value.get("ref") or value.get("source")
    text = str(value or "").strip()
    prefix, sep, raw_slug = text.partition(".")
    if sep != ".":
        raise serializers.ValidationError(
            f"{field_name} must use itemdefinition.<slug> or itembundle.<slug>."
        )
    source_type = prefix.strip().lower().replace("-", "_")
    source_slug = raw_slug.strip()
    if source_type not in LOOT_SOURCE_MODELS or not source_slug:
        raise serializers.ValidationError(
            f"{field_name} must use itemdefinition.<slug> or itembundle.<slug>."
        )
    canonical_type = "itembundle" if LOOT_SOURCE_MODELS[source_type] is ItemBundle else "itemdefinition"
    return canonical_type, source_slug


def _lookup_world(world, model_cls):
    if getattr(world, "instance_of_id", None) and model_cls in {ItemBundle, ItemDefinition}:
        return world.instance_of
    return world


def _validate_source_ref(*, world, value: Any, field_name: str) -> str:
    source_type, source_slug = _parse_source_ref(value, field_name=field_name)
    model_cls = LOOT_SOURCE_MODELS[source_type]
    lookup_world = _lookup_world(world, model_cls)
    if not model_cls.objects.filter(world=lookup_world, slug=source_slug).exists():
        raise serializers.ValidationError(f"{field_name} does not resolve to authored loot content.")
    return f"{source_type}.{source_slug}"


def _normalize_source_pool(*, world, value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise serializers.ValidationError(f"{field_name} must be a non-empty list.")

    normalized = []
    for index, raw_entry in enumerate(value):
        entry_field = f"{field_name}[{index}]"
        if isinstance(raw_entry, str):
            normalized.append({
                "ref": _validate_source_ref(world=world, value=raw_entry, field_name=entry_field),
                "weight": 1,
            })
            continue
        if not isinstance(raw_entry, dict):
            raise serializers.ValidationError(f"{entry_field} must be a source ref or mapping.")
        unknown_fields = sorted(set(raw_entry.keys()) - {"ref", "source", "weight"})
        if unknown_fields:
            raise serializers.ValidationError(
                f"Unsupported {entry_field} field(s): {', '.join(unknown_fields)}."
            )
        source_ref = raw_entry.get("ref") or raw_entry.get("source")
        if not source_ref:
            raise serializers.ValidationError(f"{entry_field} must include ref or source.")
        weight = _coerce_int(raw_entry.get("weight", 1), f"{entry_field}.weight")
        if weight < 0:
            raise serializers.ValidationError(f"{entry_field}.weight cannot be negative.")
        normalized.append({
            "ref": _validate_source_ref(world=world, value=source_ref, field_name=f"{entry_field}.ref"),
            "weight": weight,
        })
    if not any(entry["weight"] > 0 for entry in normalized):
        raise serializers.ValidationError(f"{field_name} must include at least one positive weight.")
    return normalized


def _normalize_quantity(value: Any, *, field_name: str) -> Any:
    if value in (None, ""):
        return 1
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise serializers.ValidationError(f"{field_name} cannot be negative.")
        return value
    if isinstance(value, str):
        quantity = _coerce_int(value, field_name)
        if quantity < 0:
            raise serializers.ValidationError(f"{field_name} cannot be negative.")
        return quantity
    if isinstance(value, dict):
        normalized = copy.deepcopy(value)
        unknown_fields = sorted(set(normalized.keys()) - {"value", "min", "max"})
        if unknown_fields:
            raise serializers.ValidationError(
                f"Unsupported {field_name} field(s): {', '.join(unknown_fields)}."
            )
        if "value" in normalized:
            quantity = _coerce_int(normalized.get("value"), f"{field_name}.value")
            if quantity < 0:
                raise serializers.ValidationError(f"{field_name}.value cannot be negative.")
            return quantity
        if "min" not in normalized and "max" not in normalized:
            raise serializers.ValidationError(f"{field_name} must include value or min/max.")
        minimum = _coerce_int(normalized.get("min", 0), f"{field_name}.min")
        maximum = _coerce_int(normalized.get("max", minimum), f"{field_name}.max")
        if minimum < 0 or maximum < 0:
            raise serializers.ValidationError(f"{field_name}.min and max cannot be negative.")
        if minimum > maximum:
            raise serializers.ValidationError(f"{field_name}.min cannot be greater than max.")
        return {"min": minimum, "max": maximum}
    raise serializers.ValidationError(f"{field_name} must be an integer or a min/max mapping.")


def normalize_loot_table(
    value: Any,
    *,
    world,
    field_name: str = "loot",
    allow_inherit_definition: bool = False,
) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError(f"{field_name} must be a mapping.")

    allowed_fields = {"entries"}
    if allow_inherit_definition:
        allowed_fields.add("inherit_definition")
    unknown_fields = sorted(set(value.keys()) - allowed_fields)
    if unknown_fields:
        raise serializers.ValidationError(
            f"Unsupported {field_name} field(s): {', '.join(unknown_fields)}."
        )

    inherit_definition = True
    if "inherit_definition" in value:
        inherit_definition = _coerce_bool(
            value.get("inherit_definition"),
            f"{field_name}.inherit_definition",
        )

    raw_entries = value.get("entries", [])
    if raw_entries in (None, ""):
        raw_entries = []
    if not isinstance(raw_entries, list):
        raise serializers.ValidationError(f"{field_name}.entries must be a list.")

    entries = []
    seen_slugs = set()
    for index, raw_entry in enumerate(raw_entries):
        entry_field = f"{field_name}.entries[{index}]"
        if not isinstance(raw_entry, dict):
            raise serializers.ValidationError(f"{entry_field} must be a mapping.")
        unknown_entry_fields = sorted(set(raw_entry.keys()) - {
            "slug",
            "name",
            "source",
            "source_pool",
            "probability",
            "chance",
            "quantity",
            "count",
            "min_quantity",
            "max_quantity",
            "conditions",
            "when",
        })
        if unknown_entry_fields:
            raise serializers.ValidationError(
                f"Unsupported {entry_field} field(s): {', '.join(unknown_entry_fields)}."
            )
        slug = _slug_or_default(
            raw_entry.get("slug") or raw_entry.get("name"),
            fallback=f"entry-{index + 1}",
            field_name=f"{entry_field}.slug",
        )
        if slug in seen_slugs:
            raise serializers.ValidationError(f"Duplicate loot entry slug '{slug}'.")
        seen_slugs.add(slug)

        has_source = raw_entry.get("source") not in (None, "")
        has_source_pool = raw_entry.get("source_pool") not in (None, "")
        if has_source == has_source_pool:
            raise serializers.ValidationError(
                f"{entry_field} must specify exactly one of source or source_pool."
            )

        probability = _coerce_int(
            raw_entry.get("probability", raw_entry.get("chance", 100)),
            f"{entry_field}.probability",
        )
        if probability < 0 or probability > 100:
            raise serializers.ValidationError(f"{entry_field}.probability must be between 0 and 100.")

        if "quantity" in raw_entry:
            quantity = _normalize_quantity(raw_entry.get("quantity"), field_name=f"{entry_field}.quantity")
        elif "count" in raw_entry:
            quantity = _normalize_quantity(raw_entry.get("count"), field_name=f"{entry_field}.count")
        elif "min_quantity" in raw_entry or "max_quantity" in raw_entry:
            quantity = _normalize_quantity(
                {
                    "min": raw_entry.get("min_quantity", 1),
                    "max": raw_entry.get("max_quantity", raw_entry.get("min_quantity", 1)),
                },
                field_name=f"{entry_field}.quantity",
            )
        else:
            quantity = 1

        conditions = raw_entry.get("conditions", raw_entry.get("when", {}))
        if conditions in (None, "", []):
            conditions = {}
        try:
            validate_condition_payload(conditions, field_name=f"{entry_field}.conditions")
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))

        entry = {
            "slug": slug,
            "probability": probability,
            "quantity": quantity,
        }
        if conditions:
            entry["conditions"] = copy.deepcopy(conditions)
        if has_source:
            entry["source"] = _validate_source_ref(
                world=world,
                value=raw_entry.get("source"),
                field_name=f"{entry_field}.source",
            )
        else:
            entry["source_pool"] = _normalize_source_pool(
                world=world,
                value=raw_entry.get("source_pool"),
                field_name=f"{entry_field}.source_pool",
            )
        entries.append(entry)

    if not allow_inherit_definition:
        return {"entries": entries} if entries else {}
    if entries or not inherit_definition:
        return {
            "inherit_definition": inherit_definition,
            "entries": entries,
        }
    return {}


def merge_loot_tables(definition_loot: Any, spawn_entry_loot: Any) -> dict[str, Any]:
    definition_entries = []
    if isinstance(definition_loot, dict):
        definition_entries = copy.deepcopy(definition_loot.get("entries") or [])

    if not isinstance(spawn_entry_loot, dict) or not spawn_entry_loot:
        return {"entries": definition_entries} if definition_entries else {}

    entry_entries = copy.deepcopy(spawn_entry_loot.get("entries") or [])
    inherit_definition = bool(spawn_entry_loot.get("inherit_definition", True))
    entries = [*(definition_entries if inherit_definition else []), *entry_entries]
    return {"entries": entries} if entries else {}


def _roll_quantity(quantity: Any, rng: random.Random) -> int:
    if isinstance(quantity, dict):
        minimum = max(0, int(quantity.get("min", 0) or 0))
        maximum = max(minimum, int(quantity.get("max", minimum) or minimum))
        return rng.randint(minimum, maximum)
    return max(0, int(quantity or 0))


def _choose_source_ref(entry: dict[str, Any], rng: random.Random) -> str:
    if entry.get("source"):
        return str(entry["source"])
    weighted = []
    total = 0
    for option in entry.get("source_pool") or []:
        weight = int(option.get("weight", 1) or 1)
        if weight <= 0:
            continue
        weighted.append((str(option.get("ref") or ""), weight))
        total += weight
    if not weighted:
        return ""
    target = rng.randint(1, total)
    cumulative = 0
    for source_ref, weight in weighted:
        cumulative += weight
        if target <= cumulative:
            return source_ref
    return weighted[-1][0]


def _resolve_source(*, world, source_ref: str):
    source_type, source_slug = _parse_source_ref(source_ref, field_name="loot.source")
    model_cls = LOOT_SOURCE_MODELS[source_type]
    lookup_world = _lookup_world(world, model_cls)
    return model_cls.objects.filter(world=lookup_world, slug=source_slug).first()


def _loot_context(*, mob, killer, room, entry: dict[str, Any]) -> ConditionContext:
    event_data = {
        "loot": {
            "entry_slug": entry.get("slug"),
        },
        "target": {
            "id": mob.id,
            "key": mob.key,
            "name": mob.name,
            "level": mob.level,
            "definition_slug": getattr(mob.definition, "slug", "") if mob.definition_id else "",
        },
    }
    return ConditionContext(
        actor=killer,
        player=killer,
        room=room,
        zone=getattr(room, "zone", None),
        world=mob.world,
        template=mob.definition,
        event_data=event_data,
    )


def _conditions_pass(*, mob, killer, room, entry: dict[str, Any]) -> bool:
    conditions = entry.get("conditions") or {}
    if not conditions:
        return True
    return evaluate_condition(
        conditions,
        context=_loot_context(mob=mob, killer=killer, room=room, entry=entry),
    )


def _source_world_for_mob(mob):
    if getattr(mob, "definition_id", None):
        return mob.definition.world
    return getattr(mob.world, "context", None) or mob.world


def _annotate_loot_metadata(items: list, *, mob, entry: dict[str, Any], source_ref: str) -> None:
    metadata = {
        "mob_id": mob.id,
        "mob_definition_slug": getattr(mob.definition, "slug", "") if mob.definition_id else "",
        "entry_slug": entry.get("slug"),
        "source": source_ref,
    }
    for item in items:
        roll_metadata = item.roll_metadata if isinstance(item.roll_metadata, dict) else {}
        item.roll_metadata = {
            **roll_metadata,
            "loot": metadata,
        }
        item.save(update_fields=["roll_metadata", "modified_ts"])


def _spawn_loot_source(*, source, corpse, mob, entry: dict[str, Any], source_ref: str, rng: random.Random) -> list:
    spawned = source.spawn(
        target=corpse,
        spawn_world=mob.world,
        rng=rng,
        rule=None,
    )
    if not isinstance(spawned, list):
        spawned = [spawned]
    _annotate_loot_metadata(spawned, mob=mob, entry=entry, source_ref=source_ref)
    return spawned


def roll_mob_loot(*, mob, corpse, killer, room, rng: random.Random | None = None) -> list:
    loot = mob.loot if isinstance(mob.loot, dict) else {}
    entries = loot.get("entries") or []
    if not entries:
        return []

    if rng is None:
        seed = hashlib.sha256(
            f"mob-loot:{mob.world_id}:{mob.id}:{mob.created_ts.isoformat()}".encode("utf-8")
        ).hexdigest()
        rng = random.Random(seed)

    source_world = _source_world_for_mob(mob)
    spawned_items = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        probability = int(entry.get("probability", 100) or 100)
        if probability <= 0 or rng.randint(1, 100) > probability:
            continue
        if not _conditions_pass(mob=mob, killer=killer, room=room, entry=entry):
            continue
        quantity = _roll_quantity(entry.get("quantity", 1), rng)
        for _index in range(quantity):
            source_ref = _choose_source_ref(entry, rng)
            if not source_ref:
                continue
            source = _resolve_source(world=source_world, source_ref=source_ref)
            if source is None:
                continue
            spawned_items.extend(
                _spawn_loot_source(
                    source=source,
                    corpse=corpse,
                    mob=mob,
                    entry=entry,
                    source_ref=source_ref,
                    rng=rng,
                )
            )
    return spawned_items
