from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0212_alter_itemtemplate_options_alter_mobtemplate_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemtemplate",
            name="weapon_damage",
            field=models.FloatField(default=0),
        ),
    ]
