# Generated manually during WR2 mob trait implementation.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0128_spawn_placement_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="mob",
            name="trait_instances",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
