from django.db import migrations, models

import core.leveling


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0094_worldconfig_combat_system"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldconfig",
            name="starting_level",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="worldconfig",
            name="leveling_curve",
            field=models.JSONField(default=core.leveling.default_leveling_curve),
        ),
        migrations.AddField(
            model_name="worldconfig",
            name="max_level",
            field=models.PositiveIntegerField(default=20),
        ),
    ]
