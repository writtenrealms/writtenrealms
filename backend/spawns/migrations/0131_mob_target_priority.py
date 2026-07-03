from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0130_mob_ability_runtime"),
    ]

    operations = [
        migrations.AddField(
            model_name="mob",
            name="target_priority",
            field=models.IntegerField(default=0),
        ),
    ]
