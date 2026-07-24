import django.db.models.deletion
from django.db import migrations, models


def move_authored_state_to_initial_defaults(apps, schema_editor):
    WorldState = apps.get_model("worlds", "WorldState")
    ZoneState = apps.get_model("worlds", "ZoneState")
    RoomState = apps.get_model("worlds", "RoomState")

    for row in WorldState.objects.filter(world__context_id__isnull=True).iterator():
        data = row.data if isinstance(row.data, dict) else {}
        if data:
            row.world.initial_state = data
            row.world.save(update_fields=["initial_state"])
        row.delete()

    for row in ZoneState.objects.select_related("zone").iterator():
        data = row.data if isinstance(row.data, dict) else {}
        if data:
            row.zone.initial_state = data
            row.zone.save(update_fields=["initial_state"])
        row.delete()

    for row in RoomState.objects.select_related("room").iterator():
        data = row.data if isinstance(row.data, dict) else {}
        if data:
            row.room.initial_state = data
            row.room.save(update_fields=["initial_state"])
        row.delete()


def restore_legacy_authored_state_rows(apps, schema_editor):
    World = apps.get_model("worlds", "World")
    Zone = apps.get_model("worlds", "Zone")
    Room = apps.get_model("worlds", "Room")
    WorldState = apps.get_model("worlds", "WorldState")
    ZoneState = apps.get_model("worlds", "ZoneState")
    RoomState = apps.get_model("worlds", "RoomState")

    # The old schema had one shared row per authored zone/room and cannot
    # represent parallel runtime values. On rollback, restore the authored
    # defaults rather than selecting an arbitrary live run.
    ZoneState.objects.all().delete()
    RoomState.objects.all().delete()
    for zone in Zone.objects.exclude(initial_state={}).iterator():
        data = zone.initial_state if isinstance(zone.initial_state, dict) else {}
        if data:
            ZoneState.objects.create(zone=zone, world=None, data=data)
    for room in Room.objects.exclude(initial_state={}).iterator():
        data = room.initial_state if isinstance(room.initial_state, dict) else {}
        if data:
            RoomState.objects.create(room=room, world=None, data=data)
    for world in World.objects.filter(context_id__isnull=True).exclude(
        initial_state={},
    ).iterator():
        data = world.initial_state if isinstance(world.initial_state, dict) else {}
        if data:
            WorldState.objects.update_or_create(
                world=world,
                defaults={"data": data},
            )


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0115_remove_worldconfig_death_gold_penalty_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="world",
            name="initial_state",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="zone",
            name="initial_state",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="room",
            name="initial_state",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="zonestate",
            name="world",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="zone_state_records",
                to="worlds.world",
            ),
        ),
        migrations.AddField(
            model_name="roomstate",
            name="world",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="room_state_records",
                to="worlds.world",
            ),
        ),
        migrations.AlterField(
            model_name="zonestate",
            name="zone",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="runtime_state_records",
                to="worlds.zone",
            ),
        ),
        migrations.AlterField(
            model_name="roomstate",
            name="room",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="runtime_state_records",
                to="worlds.room",
            ),
        ),
        migrations.RunPython(
            move_authored_state_to_initial_defaults,
            restore_legacy_authored_state_rows,
        ),
        migrations.AlterField(
            model_name="zonestate",
            name="world",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="zone_state_records",
                to="worlds.world",
            ),
        ),
        migrations.AlterField(
            model_name="roomstate",
            name="world",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="room_state_records",
                to="worlds.world",
            ),
        ),
        migrations.AddConstraint(
            model_name="zonestate",
            constraint=models.UniqueConstraint(
                fields=("world", "zone"),
                name="worlds_zone_state_runtime_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="roomstate",
            constraint=models.UniqueConstraint(
                fields=("world", "room"),
                name="worlds_room_state_runtime_owner",
            ),
        ),
        migrations.AlterModelOptions(
            name="zonestate",
            options={"ordering": ["world_id", "zone_id"]},
        ),
        migrations.AlterModelOptions(
            name="roomstate",
            options={"ordering": ["world_id", "room_id"]},
        ),
    ]
