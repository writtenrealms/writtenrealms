from django.db import migrations, models


def populate_player_creation(apps, schema_editor):
    Faction = apps.get_model("builders", "Faction")
    World = apps.get_model("worlds", "World")
    WorldConfig = apps.get_model("worlds", "WorldConfig")

    for config in WorldConfig.objects.all().iterator(chunk_size=500):
        world = (
            World.objects
            .filter(config=config, context__isnull=True)
            .order_by("id")
            .first()
        )
        if world is None:
            continue

        core_factions = Faction.objects.filter(
            world=world,
            type="core",
        ).order_by("created_ts", "id")
        options = list(
            core_factions
            .filter(playable=True)
            .values_list("code", flat=True)
        )
        default = (
            core_factions
            .filter(is_default=True)
            .values_list("code", flat=True)
            .first()
        )
        if default is None and options:
            default = options[0]

        if not options and not default:
            mode = "none"
        elif config.can_select_faction:
            mode = "choose_required"
        else:
            mode = "fixed_default"

        core_faction = {"mode": mode}
        if default:
            core_faction["default"] = default
        if options:
            core_faction["options"] = options

        config.player_creation = {"core_faction": core_faction}
        config.save(update_fields=["player_creation"])


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0232_faction_type_and_assignment_source"),
        ("worlds", "0104_instance_run_participants"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldconfig",
            name="player_creation",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(populate_player_creation, migrations.RunPython.noop),
    ]
