from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0131_mob_target_priority"),
    ]

    operations = [
        migrations.AddField(
            model_name="combatencounter",
            name="faceoff_override",
            field=models.BooleanField(default=False),
        ),
    ]
