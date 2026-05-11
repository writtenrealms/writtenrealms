from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0110_item_weapon_damage"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="known_abilities",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="player",
            name="ability_cooldowns",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="combatencounter",
            name="pending_player_ability",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="combatencounter",
            name="active_effects",
            field=models.JSONField(default=list),
        ),
    ]
