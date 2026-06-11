from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0226_itemtemplate_armor_and_optional_armor_class"),
    ]

    operations = [
        migrations.AddField(
            model_name="mobtemplate",
            name="weapon_damage",
            field=models.FloatField(default=0),
        ),
    ]
