from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0124_item_armor_and_optional_armor_class"),
    ]

    operations = [
        migrations.AddField(
            model_name="mob",
            name="weapon_damage",
            field=models.FloatField(default=0),
        ),
    ]
