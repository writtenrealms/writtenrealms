from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0125_mob_weapon_damage"),
    ]

    operations = [
        migrations.AddField(
            model_name="combatencounter",
            name="initiative_order",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="combatencounter",
            name="opening_priority",
            field=models.JSONField(default=list),
        ),
    ]
