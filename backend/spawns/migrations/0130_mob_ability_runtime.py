# Generated manually during WR2 mob ability loadout implementation.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0129_mob_trait_instances"),
    ]

    operations = [
        migrations.AddField(
            model_name="mob",
            name="ability_cooldowns",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="combatencounter",
            name="pending_mob_ability",
            field=models.JSONField(default=dict),
        ),
    ]
