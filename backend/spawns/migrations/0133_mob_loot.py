from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0132_combatencounter_faceoff_override"),
    ]

    operations = [
        migrations.AddField(
            model_name="mob",
            name="loot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
