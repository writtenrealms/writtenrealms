import django.db.models.deletion
from django.db import migrations, models


REVERSE_DIRECTIONS = {
    "north": "south",
    "east": "west",
    "south": "north",
    "west": "east",
    "up": "down",
    "down": "up",
}


def create_canonical_doorways(apps, schema_editor):
    Door = apps.get_model("worlds", "Door")
    Doorway = apps.get_model("worlds", "Doorway")
    database = schema_editor.connection.alias

    seen_faces: dict[tuple[int, str], int] = {}
    doors = list(
        Door.objects.using(database)
        .select_related("from_room", "to_room", "key")
        .order_by("id")
    )
    door_lookup = {
        (door.from_room_id, door.to_room_id, door.direction): door
        for door in doors
    }
    for door in doors:
        face_key = (door.from_room_id, door.direction)
        duplicate_id = seen_faces.get(face_key)
        if duplicate_id is not None:
            raise RuntimeError(
                "Cannot canonicalize WR2 doorways: doors "
                f"{duplicate_id} and {door.id} both occupy "
                f"room {door.from_room_id} direction {door.direction}."
            )
        seen_faces[face_key] = door.id
        if door.from_room_id == door.to_room_id:
            raise RuntimeError(
                "Cannot canonicalize WR2 doorways: "
                f"door {door.id} connects room {door.from_room_id} to itself."
            )
        if door.from_room.world_id != door.to_room.world_id:
            raise RuntimeError(
                "Cannot canonicalize a WR2 door across authored worlds. "
                f"Review door id: {door.id}."
            )
        if door.key_id and door.key.world_id != door.from_room.world_id:
            raise RuntimeError(
                "Cannot canonicalize a WR2 door with a key from another world. "
                f"Review door id: {door.id}."
            )

    assigned_ids: set[int] = set()
    for door in doors:
        if door.id in assigned_ids:
            continue

        reverse_direction = REVERSE_DIRECTIONS.get(door.direction)
        reverse = door_lookup.get(
            (door.to_room_id, door.from_room_id, reverse_direction),
        )
        if reverse is not None and reverse.id in assigned_ids:
            reverse = None
        faces = [door, *([reverse] if reverse is not None else [])]
        configs = {
            (
                face.key_id,
                bool(face.destroy_key),
                face.default_state,
            )
            for face in faces
        }
        if len(configs) != 1:
            ids = ", ".join(str(face.id) for face in faces)
            raise RuntimeError(
                "Cannot canonicalize WR2 doorway faces with conflicting "
                f"key/default settings. Review door ids: {ids}."
            )
        if any(face.from_room.world_id != door.from_room.world_id for face in faces):
            ids = ", ".join(str(face.id) for face in faces)
            raise RuntimeError(
                "Cannot canonicalize WR2 doorway faces across authored worlds. "
                f"Review door ids: {ids}."
            )

        doorway = Doorway.objects.using(database).create(
            world_id=door.from_room.world_id,
            key_id=door.key_id,
            destroy_key=door.destroy_key,
            default_state=door.default_state,
        )
        face_ids = [face.id for face in faces]
        Door.objects.using(database).filter(id__in=face_ids).update(
            doorway_id=doorway.id,
        )
        assigned_ids.update(face_ids)


def restore_directional_door_config(apps, schema_editor):
    Door = apps.get_model("worlds", "Door")
    Doorway = apps.get_model("worlds", "Doorway")
    database = schema_editor.connection.alias

    for doorway in (
        Doorway.objects.using(database)
        .only("id", "key_id", "destroy_key", "default_state")
        .iterator(chunk_size=1_000)
    ):
        Door.objects.using(database).filter(doorway_id=doorway.id).update(
            key_id=doorway.key_id,
            destroy_key=doorway.destroy_key,
            default_state=doorway.default_state,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0253_mob_initial_state"),
        ("worlds", "0121_reconcile_death_routing_schema"),
    ]

    operations = [
        migrations.CreateModel(
            name="Doorway",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_ts",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "modified_ts",
                    models.DateTimeField(auto_now=True, db_index=True),
                ),
                ("destroy_key", models.BooleanField(default=False)),
                (
                    "default_state",
                    models.TextField(
                        choices=[
                            ("open", "Open"),
                            ("closed", "Closed"),
                            ("locked", "Locked"),
                        ],
                        default="closed",
                    ),
                ),
                (
                    "key",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="key_doorways",
                        to="builders.itemdefinition",
                    ),
                ),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="doorways",
                        to="worlds.world",
                    ),
                ),
            ],
            options={"ordering": ["created_ts"]},
        ),
        migrations.AddField(
            model_name="door",
            name="doorway",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="faces",
                to="worlds.doorway",
            ),
        ),
        migrations.RunPython(
            create_canonical_doorways,
            reverse_code=restore_directional_door_config,
        ),
        migrations.AlterField(
            model_name="door",
            name="doorway",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="faces",
                to="worlds.doorway",
            ),
        ),
        migrations.RemoveField(
            model_name="door",
            name="default_state",
        ),
        migrations.RemoveField(
            model_name="door",
            name="destroy_key",
        ),
        migrations.RemoveField(
            model_name="door",
            name="key",
        ),
        migrations.AddConstraint(
            model_name="door",
            constraint=models.UniqueConstraint(
                fields=("from_room", "direction"),
                name="worlds_door_unique_room_direction",
            ),
        ),
        migrations.AddConstraint(
            model_name="door",
            constraint=models.UniqueConstraint(
                fields=("doorway", "from_room"),
                name="worlds_door_unique_doorway_room",
            ),
        ),
        migrations.AddConstraint(
            model_name="door",
            constraint=models.CheckConstraint(
                condition=~models.Q(("from_room", models.F("to_room"))),
                name="worlds_door_distinct_rooms",
            ),
        ),
    ]
