from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0257_trainer_profiles"),
    ]

    operations = [
        migrations.AddField(
            model_name="trainerprofile",
            name="learning",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
