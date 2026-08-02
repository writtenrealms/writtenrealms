import re
from itertools import islice

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


TARGET_BATCH_SIZE = 500

_ROOM_RELATIVE_RE = re.compile(r"^room@(\d+)$", re.IGNORECASE)
_ROOM_COORDINATE_RE = re.compile(
    r"^room@\s*([+-]?\d+)\s*,\s*([+-]?\d+)\s*,\s*([+-]?\d+)$",
    re.IGNORECASE,
)
_ROOM_DATABASE_RE = re.compile(r"^room\.(\d+)$", re.IGNORECASE)


def _batches(iterable, size):
    iterator = iter(iterable)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


def _has_value(value):
    return value is not None and value != ""


def _scalar_target(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("target is empty")
    # Before this migration every scalar target was interpreted as a room,
    # including room names that happened to begin with another ref prefix.
    # Typed scalar dispatch belongs to the new manifest parser, not this
    # historical data conversion.
    return "room", [text]


def _target_kind_and_values(target):
    if isinstance(target, str):
        return _scalar_target(target)
    if not isinstance(target, dict):
        raise ValueError("target must be a string or mapping")

    targets = []
    room_values = [
        target[field]
        for field in ("room", "room_ref")
        if field in target and _has_value(target[field])
    ]
    if room_values:
        targets.append(("room", room_values))
    if _has_value(target.get("zone")):
        targets.append(("zone", [target["zone"]]))
    if _has_value(target.get("path")):
        targets.append(("path", [target["path"]]))

    entry_values = [
        target[field]
        for field in ("entry", "parent_entry")
        if field in target and _has_value(target[field])
    ]
    if entry_values:
        targets.append(("entry", entry_values))

    if len(targets) != 1:
        raise ValueError(
            "target must identify exactly one room, zone, path, or entry"
        )
    return targets[0]


def _positive_integer(raw_value, *, description):
    if isinstance(raw_value, bool):
        raise ValueError(f"{description} must be a positive integer")
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"{description} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{description} must be a positive integer")
    return value


def _room_locator(value):
    if isinstance(value, bool):
        raise ValueError("room reference cannot be boolean")
    if isinstance(value, int):
        return "id", _positive_integer(value, description="room id")

    text = str(value or "").strip()
    if not text:
        raise ValueError("room reference is empty")
    if text.isdigit():
        return "id", _positive_integer(text, description="room id")

    coordinate_match = _ROOM_COORDINATE_RE.fullmatch(text)
    if coordinate_match:
        return "coordinates", tuple(
            int(part) for part in coordinate_match.groups()
        )
    relative_match = _ROOM_RELATIVE_RE.fullmatch(text)
    if relative_match:
        return "relative_id", _positive_integer(
            relative_match.group(1),
            description="room relative id",
        )
    database_match = _ROOM_DATABASE_RE.fullmatch(text)
    if database_match:
        return "id", _positive_integer(
            database_match.group(1),
            description="room database id",
        )
    if text.lower().startswith("room@"):
        raise ValueError(f"malformed room reference {text!r}")
    return "name", text


def _world_entity_locator(value, *, entity_type):
    if isinstance(value, bool):
        raise ValueError(f"{entity_type} reference cannot be boolean")
    if isinstance(value, int):
        return "id", _positive_integer(
            value,
            description=f"{entity_type} id",
        )

    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{entity_type} reference is empty")
    if text.isdigit():
        return "id", _positive_integer(
            text,
            description=f"{entity_type} id",
        )

    lower = text.lower()
    relative_prefix = f"{entity_type}@"
    database_prefix = f"{entity_type}."
    if lower.startswith(relative_prefix):
        return "relative_id", _positive_integer(
            text[len(relative_prefix):].strip(),
            description=f"{entity_type} relative id",
        )
    if lower.startswith(database_prefix):
        return "id", _positive_integer(
            text[len(database_prefix):].strip(),
            description=f"{entity_type} database id",
        )
    return "name", text


def _entry_locator(value):
    if isinstance(value, bool):
        raise ValueError("entry reference cannot be boolean")
    text = str(value or "").strip()
    if not text:
        raise ValueError("entry reference is empty")
    lower = text.lower()
    if lower.startswith("entry.") or lower.startswith("entry@"):
        text = text[6:].strip()
    if not text:
        raise ValueError("entry reference has no slug")
    return "slug", text


def _reference_key(*, kind, scope_id, value):
    if kind == "room":
        locator_kind, locator_value = _room_locator(value)
    elif kind in {"zone", "path"}:
        locator_kind, locator_value = _world_entity_locator(
            value,
            entity_type=kind,
        )
    else:
        locator_kind, locator_value = _entry_locator(value)
    return kind, scope_id, locator_kind, locator_value


def _world_entity_reference_ids(
    *,
    Model,
    database,
    kind,
    requests,
    include_coordinates=False,
):
    kind_requests = {request for request in requests if request[0] == kind}
    if not kind_requests:
        return {}

    world_ids = {request[1] for request in kind_requests}
    database_ids = {
        request[3] for request in kind_requests if request[2] == "id"
    }
    relative_ids = {
        request[3]
        for request in kind_requests
        if request[2] == "relative_id"
    }
    names = {
        request[3] for request in kind_requests if request[2] == "name"
    }

    candidate_filter = Q()
    if database_ids:
        candidate_filter |= Q(id__in=database_ids)
    if relative_ids:
        candidate_filter |= Q(relative_id__in=relative_ids)
    if names:
        candidate_filter |= Q(name__in=names)

    coordinates = set()
    if include_coordinates:
        coordinates = {
            request[3]
            for request in kind_requests
            if request[2] == "coordinates"
        }
        if coordinates:
            candidate_filter |= Q(
                x__in={value[0] for value in coordinates},
                y__in={value[1] for value in coordinates},
                z__in={value[2] for value in coordinates},
            )

    fields = ["id", "world_id", "relative_id", "name"]
    if include_coordinates:
        fields.extend(["x", "y", "z"])
    rows = (
        Model.objects.using(database)
        .filter(world_id__in=world_ids)
        .filter(candidate_filter)
        .order_by("id")
        .values(*fields)
    )

    resolved = {}
    for row in rows.iterator(chunk_size=TARGET_BATCH_SIZE):
        aliases = [
            (kind, row["world_id"], "id", row["id"]),
            (
                kind,
                row["world_id"],
                "relative_id",
                row["relative_id"],
            ),
        ]
        if row["name"]:
            aliases.append((kind, row["world_id"], "name", row["name"]))
        if include_coordinates:
            aliases.append(
                (
                    kind,
                    row["world_id"],
                    "coordinates",
                    (row["x"], row["y"], row["z"]),
                )
            )
        for alias in aliases:
            if alias in kind_requests:
                # Historical name lookup selected the lowest database id.
                resolved.setdefault(alias, row["id"])
    return resolved


def _entry_reference_ids(*, SpawnEntry, database, requests):
    entry_requests = {
        request for request in requests if request[0] == "entry"
    }
    if not entry_requests:
        return {}
    plan_ids = {request[1] for request in entry_requests}
    slugs = {request[3] for request in entry_requests}
    rows = (
        SpawnEntry.objects.using(database)
        .filter(plan_id__in=plan_ids, slug__in=slugs)
        .order_by("id")
        .values("id", "plan_id", "slug")
    )
    resolved = {}
    for row in rows.iterator(chunk_size=TARGET_BATCH_SIZE):
        key = ("entry", row["plan_id"], "slug", row["slug"])
        if key in entry_requests:
            resolved.setdefault(key, row["id"])
    return resolved


def _migration_error(entry, message):
    return RuntimeError(
        "Cannot migrate SpawnEntry "
        f"{entry.id} ({entry.slug!r}, plan {entry.plan_id}) target "
        f"{entry.target!r}: {message}. Correct the target and retry."
    )


def _validate_entry_dependency(*, entry, parent):
    if parent.plan_id != entry.plan_id:
        raise ValueError("entry target belongs to a different spawn plan")
    if parent.order >= entry.order:
        raise ValueError("entry target must have a lower order")
    if entry.is_active and not parent.is_active:
        raise ValueError("an active entry must target an active entry")


def migrate_spawn_entry_targets(apps, schema_editor):
    SpawnEntry = apps.get_model("builders", "SpawnEntry")
    Path = apps.get_model("builders", "Path")
    Room = apps.get_model("worlds", "Room")
    Zone = apps.get_model("worlds", "Zone")
    database = schema_editor.connection.alias

    entries = (
        SpawnEntry.objects.using(database)
        .select_related("plan")
        .order_by("id")
        .iterator(chunk_size=TARGET_BATCH_SIZE)
    )
    for batch in _batches(entries, TARGET_BATCH_SIZE):
        parsed_targets = []
        requests = set()
        for entry in batch:
            try:
                kind, values = _target_kind_and_values(entry.target)
                scope_id = (
                    entry.plan_id
                    if kind == "entry"
                    else entry.plan.world_id
                )
                keys = [
                    _reference_key(
                        kind=kind,
                        scope_id=scope_id,
                        value=value,
                    )
                    for value in values
                ]
            except ValueError as exc:
                raise _migration_error(entry, str(exc)) from exc
            parsed_targets.append((entry, kind, keys))
            requests.update(keys)

        resolved = {}
        resolved.update(
            _world_entity_reference_ids(
                Model=Room,
                database=database,
                kind="room",
                requests=requests,
                include_coordinates=True,
            )
        )
        resolved.update(
            _world_entity_reference_ids(
                Model=Zone,
                database=database,
                kind="zone",
                requests=requests,
            )
        )
        resolved.update(
            _world_entity_reference_ids(
                Model=Path,
                database=database,
                kind="path",
                requests=requests,
            )
        )
        resolved.update(
            _entry_reference_ids(
                SpawnEntry=SpawnEntry,
                database=database,
                requests=requests,
            )
        )
        parent_entries = SpawnEntry.objects.using(database).in_bulk({
            target_id
            for request, target_id in resolved.items()
            if request[0] == "entry"
        })

        for entry, kind, keys in parsed_targets:
            resolved_ids = [resolved.get(key) for key in keys]
            if any(value is None for value in resolved_ids):
                missing = [key for key in keys if resolved.get(key) is None]
                raise _migration_error(
                    entry,
                    f"reference(s) {missing!r} do not resolve in their world or plan",
                )
            if len(set(resolved_ids)) != 1:
                raise _migration_error(
                    entry,
                    "its legacy aliases resolve to different targets",
                )
            target_id = resolved_ids[0]
            if kind == "entry":
                try:
                    _validate_entry_dependency(
                        entry=entry,
                        parent=parent_entries[target_id],
                    )
                except (KeyError, ValueError) as exc:
                    raise _migration_error(entry, str(exc)) from exc
            entry.target_room_id = target_id if kind == "room" else None
            entry.target_zone_id = target_id if kind == "zone" else None
            entry.target_path_id = target_id if kind == "path" else None
            entry.target_entry_id = target_id if kind == "entry" else None

        SpawnEntry.objects.using(database).bulk_update(
            batch,
            [
                "target_room",
                "target_zone",
                "target_path",
                "target_entry",
            ],
            batch_size=TARGET_BATCH_SIZE,
        )


def restore_spawn_entry_target_json(apps, schema_editor):
    SpawnEntry = apps.get_model("builders", "SpawnEntry")
    database = schema_editor.connection.alias
    entries = (
        SpawnEntry.objects.using(database)
        .select_related(
            "target_room",
            "target_zone",
            "target_path",
            "target_entry",
        )
        .order_by("id")
        .iterator(chunk_size=TARGET_BATCH_SIZE)
    )
    for batch in _batches(entries, TARGET_BATCH_SIZE):
        for entry in batch:
            if entry.target_room_id:
                relative_id = entry.target_room.relative_id
                reference = (
                    f"room@{relative_id}"
                    if relative_id
                    else f"room.{entry.target_room_id}"
                )
                entry.target = {"room": reference}
            elif entry.target_zone_id:
                relative_id = (
                    entry.target_zone.relative_id or entry.target_zone_id
                )
                entry.target = {"zone": f"zone@{relative_id}"}
            elif entry.target_path_id:
                relative_id = (
                    entry.target_path.relative_id or entry.target_path_id
                )
                entry.target = {"path": f"path@{relative_id}"}
            elif entry.target_entry_id:
                entry.target = {"entry": entry.target_entry.slug}
            else:
                raise RuntimeError(
                    f"SpawnEntry {entry.id} has no relational target."
                )
        SpawnEntry.objects.using(database).bulk_update(
            batch,
            ["target"],
            batch_size=TARGET_BATCH_SIZE,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0254_canonicalize_authored_room_references"),
        ("worlds", "0125_instance_template_manifest_slugs"),
    ]

    operations = [
        migrations.AddField(
            model_name="spawnentry",
            name="target_entry",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="dependent_entries",
                to="builders.spawnentry",
            ),
        ),
        migrations.AddField(
            model_name="spawnentry",
            name="target_path",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="spawn_entries",
                to="builders.path",
            ),
        ),
        migrations.AddField(
            model_name="spawnentry",
            name="target_room",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="spawn_entries",
                to="worlds.room",
            ),
        ),
        migrations.AddField(
            model_name="spawnentry",
            name="target_zone",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="spawn_entries",
                to="worlds.zone",
            ),
        ),
        migrations.RunPython(
            migrate_spawn_entry_targets,
            reverse_code=restore_spawn_entry_target_json,
        ),
        migrations.AddConstraint(
            model_name="spawnentry",
            constraint=models.CheckConstraint(
                condition=(
                    Q(
                        target_room__isnull=False,
                        target_zone__isnull=True,
                        target_path__isnull=True,
                        target_entry__isnull=True,
                    )
                    | Q(
                        target_room__isnull=True,
                        target_zone__isnull=False,
                        target_path__isnull=True,
                        target_entry__isnull=True,
                    )
                    | Q(
                        target_room__isnull=True,
                        target_zone__isnull=True,
                        target_path__isnull=False,
                        target_entry__isnull=True,
                    )
                    | Q(
                        target_room__isnull=True,
                        target_zone__isnull=True,
                        target_path__isnull=True,
                        target_entry__isnull=False,
                    )
                ),
                name="builders_spawn_entry_exactly_one_target",
            ),
        ),
        migrations.RemoveField(
            model_name="spawnentry",
            name="target",
        ),
    ]
