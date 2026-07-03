from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0105_worldconfig_player_creation"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldconfig",
            name="default_roam_chance",
            field=models.PositiveIntegerField(default=10),
        ),
    ]
