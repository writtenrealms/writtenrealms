from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0223_mobdefinition_trainer"),
    ]

    operations = [
        migrations.AddField(
            model_name="abilitydefinition",
            name="cast_time",
            field=models.JSONField(default=dict),
        ),
    ]
