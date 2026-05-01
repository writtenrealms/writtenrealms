from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0092_worldconfig_combat_resolution_interval"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldconfig",
            name="stat_system",
            field=models.JSONField(default=dict),
        ),
    ]
