from django.core.validators import MaxValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0129_zone_runtime_policies"),
    ]

    operations = [
        migrations.AddField(
            model_name="zone",
            name="default_roam_chance",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[MaxValueValidator(100)],
            ),
        ),
    ]
