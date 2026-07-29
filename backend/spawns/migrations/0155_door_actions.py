import django.db.models.deletion
from django.db import migrations, models
from itertools import groupby


def canonicalize_runtime_door_states(apps, schema_editor):
    DoorState = apps.get_model("spawns", "DoorState")
    database = schema_editor.connection.alias

    state_rows = (
        DoorState.objects.using(database)
        .values("id", "world_id", "state", "door__doorway_id")
        .order_by("world_id", "door__doorway_id", "id")
        .iterator(chunk_size=2_000)
    )
    for (world_id, doorway_id), grouped_rows in groupby(
        state_rows,
        key=lambda row: (row["world_id"], row["door__doorway_id"]),
    ):
        states = list(grouped_rows)
        distinct_states = {state["state"] for state in states}
        if len(distinct_states) != 1:
            ids = ", ".join(str(state["id"]) for state in states)
            raise RuntimeError(
                "Cannot canonicalize conflicting WR2 runtime door states for "
                f"world {world_id}, doorway {doorway_id}. Review state ids: {ids}."
            )
        keeper, *duplicates = states
        DoorState.objects.using(database).filter(id=keeper["id"]).update(
            doorway_id=doorway_id,
        )
        if duplicates:
            DoorState.objects.using(database).filter(
                id__in=[state["id"] for state in duplicates],
            ).delete()


def restore_directional_door_states(apps, schema_editor):
    Door = apps.get_model("worlds", "Door")
    DoorState = apps.get_model("spawns", "DoorState")
    database = schema_editor.connection.alias

    states = (
        DoorState.objects.using(database)
        .values(
            "id",
            "world_id",
            "doorway_id",
            "state",
            "revision",
        )
        .order_by("id")
        .iterator(chunk_size=2_000)
    )
    for state in states:
        face_ids = list(
            Door.objects.using(database)
            .filter(doorway_id=state["doorway_id"])
            .order_by("id")
            .values_list("id", flat=True)
        )
        if not face_ids:
            raise RuntimeError(
                "Cannot restore directional WR2 door state because doorway "
                f"{state['doorway_id']} has no faces."
            )

        DoorState.objects.using(database).filter(id=state["id"]).update(
            door_id=face_ids[0],
        )
        for face_id in face_ids[1:]:
            DoorState.objects.using(database).create(
                door_id=face_id,
                doorway_id=state["doorway_id"],
                world_id=state["world_id"],
                state=state["state"],
                revision=state["revision"],
            )


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0154_reconcile_death_receipt_schema"),
        ("worlds", "0122_canonical_doorways"),
    ]

    operations = [
        migrations.AddField(
            model_name="doorstate",
            name="doorway",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="runtime_states",
                to="worlds.doorway",
            ),
        ),
        migrations.AddField(
            model_name="doorstate",
            name="revision",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="doorstate",
            name="door",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="door_states",
                to="worlds.door",
            ),
        ),
        migrations.RunPython(
            canonicalize_runtime_door_states,
            reverse_code=restore_directional_door_states,
        ),
        migrations.RemoveField(
            model_name="doorstate",
            name="door",
        ),
        migrations.AlterField(
            model_name="doorstate",
            name="doorway",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="runtime_states",
                to="worlds.doorway",
            ),
        ),
        migrations.AddConstraint(
            model_name="doorstate",
            constraint=models.UniqueConstraint(
                fields=("world", "doorway"),
                name="spawns_door_state_runtime_doorway",
            ),
        ),
        migrations.CreateModel(
            name="PreparedGameAction",
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
                (
                    "action_type",
                    models.TextField(
                        choices=[
                            ("open_door", "Open_door"),
                            ("close_door", "Close_door"),
                            ("lock_door", "Lock_door"),
                            ("unlock_door", "Unlock_door"),
                        ],
                    ),
                ),
                (
                    "status",
                    models.TextField(
                        choices=[
                            ("pending", "Pending"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="pending",
                    ),
                ),
                ("run_at", models.DateTimeField()),
                ("expected_revision", models.BigIntegerField(default=0)),
                ("request_id", models.UUIDField(blank=True, null=True)),
                (
                    "request_segment",
                    models.CharField(default="r", max_length=128),
                ),
                (
                    "request_selector",
                    models.TextField(blank=True, default=""),
                ),
                (
                    "target_direction",
                    models.TextField(
                        choices=[
                            ("north", "North"),
                            ("east", "East"),
                            ("south", "South"),
                            ("west", "West"),
                            ("up", "Up"),
                            ("down", "Down"),
                        ],
                    ),
                ),
                ("target_name", models.TextField(default="door")),
                (
                    "failure_code",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("result", models.JSONField(blank=True, default=dict)),
                ("completed_ts", models.DateTimeField(blank=True, null=True)),
                (
                    "doorway",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="prepared_game_actions",
                        to="worlds.doorway",
                    ),
                ),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="prepared_game_actions",
                        to="spawns.player",
                    ),
                ),
                (
                    "room",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="prepared_game_actions",
                        to="worlds.room",
                    ),
                ),
                (
                    "runtime_world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="prepared_game_actions",
                        to="worlds.world",
                    ),
                ),
            ],
            options={
                "ordering": ["created_ts"],
                "indexes": [
                    models.Index(
                        fields=["status", "run_at", "id"],
                        name="spawn_prepared_due_idx",
                    ),
                    models.Index(
                        fields=["status", "modified_ts", "id"],
                        name="spawn_prepared_prune_idx",
                    ),
                    models.Index(
                        fields=["runtime_world", "doorway", "status"],
                        name="spawn_prepared_door_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("status", "pending")),
                        fields=("player",),
                        name="spawn_prepared_player_pending_uniq",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("request_id__isnull", False)),
                        fields=("player", "request_id", "request_segment"),
                        name="spawn_prepared_request_uniq",
                    ),
                ],
            },
        ),
    ]
