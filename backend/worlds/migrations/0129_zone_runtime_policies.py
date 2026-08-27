import django.db.models.deletion
from django.db import migrations, models


ZONE_POLICY_MODE_FIXED = "fixed"
ZONE_POLICY_MODE_NONE = "none"


def migrate_zone_policies(apps, schema_editor):
    Zone = apps.get_model("worlds", "Zone")
    pending = []
    for zone in Zone.objects.all().only("id", "respawn_wait").iterator(
        chunk_size=1000,
    ):
        wait = int(zone.respawn_wait)
        if wait < 0:
            zone.respawn_mode = ZONE_POLICY_MODE_NONE
            zone.respawn_seconds = None
            zone.door_reset_mode = ZONE_POLICY_MODE_NONE
            zone.door_reset_seconds = None
        else:
            zone.respawn_mode = ZONE_POLICY_MODE_FIXED
            zone.respawn_seconds = wait
            zone.door_reset_mode = ZONE_POLICY_MODE_FIXED
            zone.door_reset_seconds = wait
        pending.append(zone)
        if len(pending) >= 1000:
            Zone.objects.bulk_update(
                pending,
                [
                    "respawn_mode",
                    "respawn_seconds",
                    "door_reset_mode",
                    "door_reset_seconds",
                ],
            )
            pending = []
    if pending:
        Zone.objects.bulk_update(
            pending,
            [
                "respawn_mode",
                "respawn_seconds",
                "door_reset_mode",
                "door_reset_seconds",
            ],
        )


def restore_respawn_wait(apps, schema_editor):
    Zone = apps.get_model("worlds", "Zone")
    pending = []
    for zone in Zone.objects.all().only(
        "id",
        "respawn_mode",
        "respawn_seconds",
    ).iterator(chunk_size=1000):
        zone.respawn_wait = (
            -1
            if zone.respawn_mode == ZONE_POLICY_MODE_NONE
            else int(zone.respawn_seconds or 0)
        )
        pending.append(zone)
        if len(pending) >= 1000:
            Zone.objects.bulk_update(pending, ["respawn_wait"])
            pending = []
    if pending:
        Zone.objects.bulk_update(pending, ["respawn_wait"])


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0128_room_trainer_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="zone",
            name="respawn_mode",
            field=models.CharField(
                choices=[("fixed", "Fixed"), ("none", "None")],
                default="fixed",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="zone",
            name="respawn_seconds",
            field=models.PositiveIntegerField(
                blank=True,
                default=300,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="zone",
            name="door_reset_mode",
            field=models.CharField(
                choices=[("fixed", "Fixed"), ("none", "None")],
                default="fixed",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="zone",
            name="door_reset_seconds",
            field=models.PositiveIntegerField(
                blank=True,
                default=300,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="zone",
            name="door_reset_policy_version",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
        migrations.CreateModel(
            name="ZoneDoorResetSchedule",
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
                    "next_reset_ts",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        null=True,
                    ),
                ),
                (
                    "policy_version",
                    models.PositiveBigIntegerField(default=0),
                ),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="zone_door_reset_schedules",
                        to="worlds.world",
                    ),
                ),
                (
                    "zone",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="runtime_door_reset_schedules",
                        to="worlds.zone",
                    ),
                ),
            ],
            options={
                "ordering": ["world_id", "zone_id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("world", "zone"),
                        name="worlds_zone_door_reset_schedule_owner",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            migrate_zone_policies,
            restore_respawn_wait,
        ),
        migrations.RemoveField(
            model_name="zone",
            name="last_respawn_ts",
        ),
        migrations.RemoveField(
            model_name="zone",
            name="respawn_wait",
        ),
        migrations.AddConstraint(
            model_name="zone",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        respawn_mode="fixed",
                        respawn_seconds__isnull=False,
                    )
                    | models.Q(
                        respawn_mode="none",
                        respawn_seconds__isnull=True,
                    )
                ),
                name="worlds_zone_respawn_policy_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="zone",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        door_reset_mode="fixed",
                        door_reset_seconds__isnull=False,
                    )
                    | models.Q(
                        door_reset_mode="none",
                        door_reset_seconds__isnull=True,
                    )
                ),
                name="worlds_zone_door_reset_policy_shape",
            ),
        ),
    ]
