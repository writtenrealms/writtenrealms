from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0122_player_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="command_history",
            field=models.JSONField(default=list),
        ),
    ]
