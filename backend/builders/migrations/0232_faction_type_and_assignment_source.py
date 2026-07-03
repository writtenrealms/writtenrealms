from django.db import migrations, models


def populate_faction_type(apps, schema_editor):
    Faction = apps.get_model("builders", "Faction")
    for faction in Faction.objects.all().iterator(chunk_size=500):
        faction_type = "core" if faction.is_core else "reputation"
        playable = bool(faction.is_selectable) if faction_type == "core" else False
        Faction.objects.filter(pk=faction.pk).update(
            type=faction_type,
            playable=playable,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0231_mobdefinition_combat_abilities"),
    ]

    operations = [
        migrations.AddField(
            model_name="faction",
            name="type",
            field=models.TextField(
                choices=[("core", "core"), ("reputation", "reputation")],
                default="reputation",
            ),
        ),
        migrations.AddField(
            model_name="faction",
            name="playable",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="faction",
            name="default_languages",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="factionassignment",
            name="source",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RunPython(populate_faction_type, migrations.RunPython.noop),
    ]
