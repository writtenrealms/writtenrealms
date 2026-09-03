from django.core.validators import MaxValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0258_trainerprofile_learning"),
    ]

    operations = [
        migrations.AddField(
            model_name="spawnplan",
            name="default_roam_chance",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[MaxValueValidator(100)],
            ),
        ),
    ]
