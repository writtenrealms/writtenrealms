from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0091_roomstate_worldstate_zonestate"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldconfig",
            name="combat_resolution_interval",
            field=models.FloatField(default=0),
        ),
    ]
