from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Any

from django.db.models import NOT_PROVIDED

from core.model_mixins import ItemMixin
from core.stat_system import get_input_attribute_order


RANDOMIZATION_VERSION = 1
RANDOMIZATION_MODES = {"uniform", "favor_low", "favor_high"}


class ItemDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class RollResult:
    input_attributes: dict[str, float]
    ignored_attributes: list[str]
    randomization_version: int
    randomized: bool


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def normalize_input_attribute_map(value: Any, *, field_name: str = "input_attributes") -> dict[str, int | float]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ItemDefinitionError(f"{field_name} must be a mapping.")

    normalized: dict[str, int | float] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        if not key:
            raise ItemDefinitionError(f"{field_name} keys must be non-empty strings.")
        if not _is_number(raw_value):
            raise ItemDefinitionError(f"{field_name}.{key} must be a number.")
        normalized[key] = raw_value
    return normalized


def _coerce_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ItemDefinitionError(f"{field_name} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ItemDefinitionError(f"{field_name} must be an integer.")


def _coerce_curve(value: Any, *, field_name: str) -> float:
    if value in (None, ""):
        return 1.0
    if isinstance(value, bool):
        raise ItemDefinitionError(f"{field_name} must be a positive number.")
    try:
        curve = float(value)
    except (TypeError, ValueError):
        raise ItemDefinitionError(f"{field_name} must be a positive number.")
    if not math.isfinite(curve) or curve <= 0:
        raise ItemDefinitionError(f"{field_name} must be a positive number.")
    return curve


def normalize_item_randomization(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ItemDefinitionError("randomization must be a mapping.")

    version = _coerce_int(
        value.get("version", RANDOMIZATION_VERSION),
        field_name="randomization.version",
    )
    if version != RANDOMIZATION_VERSION:
        raise ItemDefinitionError(
            f"Unsupported randomization.version '{version}'. Supported: {RANDOMIZATION_VERSION}."
        )

    raw_attributes = value.get("attributes", [])
    if raw_attributes in (None, ""):
        raw_attributes = []
    if not isinstance(raw_attributes, list):
        raise ItemDefinitionError("randomization.attributes must be a list.")

    attributes: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(raw_attributes):
        field_prefix = f"randomization.attributes[{index}]"
        if not isinstance(raw_entry, dict):
            raise ItemDefinitionError(f"{field_prefix} must be a mapping.")

        key = str(raw_entry.get("key") or "").strip()
        if not key:
            raise ItemDefinitionError(f"{field_prefix}.key is required.")

        minimum = _coerce_int(raw_entry.get("min"), field_name=f"{field_prefix}.min")
        maximum = _coerce_int(raw_entry.get("max"), field_name=f"{field_prefix}.max")
        if minimum > maximum:
            raise ItemDefinitionError(f"{field_prefix}.min cannot be greater than max.")

        mode = str(raw_entry.get("mode") or "uniform").strip().lower()
        if mode not in RANDOMIZATION_MODES:
            raise ItemDefinitionError(
                f"{field_prefix}.mode must be one of: {', '.join(sorted(RANDOMIZATION_MODES))}."
            )

        curve = _coerce_curve(
            raw_entry.get("curve", 1.0),
            field_name=f"{field_prefix}.curve",
        )
        entry = {
            "key": key,
            "min": minimum,
            "max": maximum,
            "mode": mode,
        }
        if curve != 1.0:
            entry["curve"] = curve
        attributes.append(entry)

    if not attributes:
        return {}
    return {
        "version": version,
        "attributes": attributes,
    }


def _weighted_choice(values: list[int], weights: list[float], rng: random.Random) -> int:
    total_weight = sum(weights)
    if total_weight <= 0:
        return values[0]
    target = rng.random() * total_weight
    cumulative = 0.0
    for value, weight in zip(values, weights):
        cumulative += weight
        if target <= cumulative:
            return value
    return values[-1]


def _roll_attribute(entry: dict[str, Any], rng: random.Random) -> int:
    minimum = int(entry["min"])
    maximum = int(entry["max"])
    mode = entry["mode"]
    if mode == "uniform":
        return rng.randint(minimum, maximum)

    values = list(range(minimum, maximum + 1))
    curve = float(entry.get("curve", 1.0))
    if mode == "favor_low":
        weights = [float(maximum - value + 1) ** curve for value in values]
    else:
        weights = [float(value - minimum + 1) ** curve for value in values]
    return _weighted_choice(values, weights, rng)


def roll_item_randomization(
    definition,
    world_stat_system: dict[str, Any],
    rng: random.Random | None = None,
) -> RollResult:
    rng = rng or random
    randomization = normalize_item_randomization(definition.randomization or {})
    declared_keys = set(get_input_attribute_order(world_stat_system))
    rolled: dict[str, float] = {}
    ignored: list[str] = []

    for entry in randomization.get("attributes", []):
        key = entry["key"]
        if key not in declared_keys:
            if key not in ignored:
                ignored.append(key)
            continue
        rolled[key] = rolled.get(key, 0.0) + float(_roll_attribute(entry, rng))

    return RollResult(
        input_attributes=rolled,
        ignored_attributes=ignored,
        randomization_version=randomization.get("version", RANDOMIZATION_VERSION),
        randomized=bool(randomization.get("attributes")),
    )


def _item_mixin_field_names() -> set[str]:
    return {field.name for field in ItemMixin._meta.fields if field.name != "id"}


ITEM_MIXIN_FIELD_NAMES = _item_mixin_field_names()


def item_definition_property_fields() -> tuple[str, ...]:
    excluded = {
        "name",
        "description",
        "ground_description",
        "keywords",
        "input_attributes",
        "type",
    }
    return tuple(sorted(ITEM_MIXIN_FIELD_NAMES - excluded))


def _default_for_field(field) -> Any:
    if field.default is not NOT_PROVIDED:
        return field.get_default()
    if field.null:
        return None
    if getattr(field, "empty_strings_allowed", False):
        return ""
    return None


def _resolve_currency(definition, value: Any):
    from builders.models import Currency

    if value in (None, ""):
        return Currency.objects.filter(world=definition.world, is_default=True).first()
    if hasattr(value, "pk"):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        currency = Currency.objects.filter(world=definition.world, pk=value).first()
        if currency:
            return currency
        return None
    text = str(value or "").strip()
    if not text:
        return Currency.objects.filter(world=definition.world, is_default=True).first()
    prefix, sep, raw = text.partition(".")
    if sep == "." and prefix == "currency":
        if raw.isdigit():
            return Currency.objects.filter(world=definition.world, pk=int(raw)).first()
        text = raw
    return Currency.objects.filter(world=definition.world, code=text).first()


def _item_fields_from_definition(definition, input_attributes: dict[str, float]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for field in ItemMixin._meta.fields:
        if field.name == "id":
            continue
        fields[field.name] = _default_for_field(field)

    fields["name"] = definition.name or fields.get("name") or "Unnamed Item"
    fields["description"] = definition.description or None
    fields["ground_description"] = definition.ground_description or None
    fields["keywords"] = definition.keywords or None
    fields["type"] = definition.item_type or fields.get("type")

    base_properties = definition.base_properties or {}
    for key, value in base_properties.items():
        if key not in ITEM_MIXIN_FIELD_NAMES or key in {"input_attributes", "name"}:
            continue
        if key == "currency":
            fields[key] = _resolve_currency(definition, value)
        else:
            fields[key] = value

    if "currency" not in base_properties:
        fields["currency"] = _resolve_currency(definition, None)

    fields["input_attributes"] = input_attributes
    return fields


def _merge_input_attributes(*maps: dict[str, Any]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for values in maps:
        normalized = normalize_input_attribute_map(values or {})
        for key, value in normalized.items():
            merged[key] = merged.get(key, 0.0) + value
    return merged


def spawn_item_from_definition(
    definition,
    target,
    spawn_world,
    *,
    rng: random.Random | None = None,
    rule=None,
    extra_roll_metadata: dict[str, Any] | None = None,
):
    from core.stat_system import get_world_stat_system
    from spawns.models import Item

    roll_result = roll_item_randomization(
        definition,
        get_world_stat_system(definition.world),
        rng=rng,
    )
    input_attributes = _merge_input_attributes(
        definition.base_input_attributes or {},
        roll_result.input_attributes,
    )
    item_fields = _item_fields_from_definition(definition, input_attributes)
    roll_metadata = {
        "source_definition_slug": definition.slug,
        "randomization_version": roll_result.randomization_version,
        "rolled_at_definition_modified_ts": definition.modified_ts.isoformat()
        if definition.modified_ts else "",
        "ignored_attributes": roll_result.ignored_attributes,
        "randomized": roll_result.randomized,
    }
    if extra_roll_metadata:
        roll_metadata.update(extra_roll_metadata)

    return Item.objects.create(
        world=spawn_world,
        container=target,
        definition=definition,
        definition_slug_snapshot=definition.slug,
        roll_metadata=roll_metadata,
        rule=rule,
        **item_fields,
    )


def sync_spawned_items_from_definition(definition) -> int:
    """
    Keep unmodified definition-backed runtime items aligned with their authoring
    definition. Randomized items keep their rolled input attributes, but still
    receive current authored properties such as name, descriptions, and damage.
    """
    from spawns.models import Item

    updated = 0
    timestamp = (
        definition.modified_ts.isoformat()
        if definition.modified_ts else ""
    )
    queryset = (
        Item.objects
        .filter(definition=definition, upgrade_count=0, augment__isnull=True)
        .select_related("currency")
    )
    for item in queryset:
        roll_metadata = item.roll_metadata if isinstance(item.roll_metadata, dict) else {}
        if roll_metadata.get("randomized"):
            input_attributes = item.input_attributes or {}
        else:
            input_attributes = _merge_input_attributes(
                definition.base_input_attributes or {},
            )
        item_fields = _item_fields_from_definition(definition, input_attributes)
        for field_name, value in item_fields.items():
            setattr(item, field_name, value)
        item.roll_metadata = {
            **roll_metadata,
            "source_definition_slug": definition.slug,
            "rolled_at_definition_modified_ts": timestamp,
        }
        item.definition_slug_snapshot = definition.slug
        item.save(
            update_fields=[
                *item_fields.keys(),
                "roll_metadata",
                "definition_slug_snapshot",
                "modified_ts",
            ]
        )
        updated += 1
    return updated


def choose_item_bundle_entry(bundle, rng: random.Random | None = None):
    rng = rng or random
    candidates = []
    for entry in bundle.entries.select_related("item_definition").all():
        probability = int(entry.probability)
        if probability < 100 and rng.random() * 100 >= probability:
            continue
        candidates.append(entry)
    if not candidates:
        return None
    weights = [float(max(0, entry.weight)) for entry in candidates]
    if not any(weights):
        weights = [1.0 for _entry in candidates]
    selected_index = _weighted_choice(list(range(len(candidates))), weights, rng)
    return candidates[selected_index]


def spawn_item_from_bundle(
    bundle,
    target,
    spawn_world,
    *,
    rng: random.Random | None = None,
    rule=None,
) -> list:
    rng = rng or random
    entry = choose_item_bundle_entry(bundle, rng=rng)
    if entry is None:
        return []
    min_quantity = max(0, int(entry.min_quantity))
    max_quantity = max(min_quantity, int(entry.max_quantity))
    quantity = rng.randint(min_quantity, max_quantity)
    bundle_roll_id = f"{bundle.id}:{entry.id}:{rng.getrandbits(64):016x}"
    return [
        spawn_item_from_definition(
            entry.item_definition,
            target,
            spawn_world,
            rng=rng,
            rule=rule,
            extra_roll_metadata={
                "source_bundle_id": bundle.id,
                "source_bundle_slug": bundle.slug,
                "source_bundle_entry_id": entry.id,
                "source_bundle_roll_id": bundle_roll_id,
            },
        )
        for _index in range(quantity)
    ]
