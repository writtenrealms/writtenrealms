from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0109_combatencounter"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="weapon_damage",
            field=models.FloatField(default=0),
        ),
    ]
