from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0093_worldconfig_stat_system"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldconfig",
            name="combat_system",
            field=models.JSONField(default=dict),
        ),
    ]
