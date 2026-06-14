from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0126_combatencounter_initiative_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="active_effects",
            field=models.JSONField(default=list),
        ),
    ]
