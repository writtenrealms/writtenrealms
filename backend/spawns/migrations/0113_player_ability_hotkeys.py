from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0112_combatencounter_pending_flee"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="ability_hotkeys",
            field=models.JSONField(default=dict),
        ),
    ]
