from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0227_mobtemplate_weapon_damage"),
    ]

    operations = [
        migrations.AddField(
            model_name="abilitydefinition",
            name="help",
            field=models.JSONField(default=dict),
        ),
    ]
