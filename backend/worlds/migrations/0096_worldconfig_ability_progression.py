from django.db import migrations, models

import core.abilities


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0095_worldconfig_leveling"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldconfig",
            name="ability_progression",
            field=models.JSONField(default=core.abilities.default_ability_progression),
        ),
    ]
