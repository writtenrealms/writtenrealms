from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0111_player_abilities_and_encounter_effects"),
    ]

    operations = [
        migrations.AddField(
            model_name="combatencounter",
            name="pending_flee",
            field=models.JSONField(default=dict),
        ),
    ]
