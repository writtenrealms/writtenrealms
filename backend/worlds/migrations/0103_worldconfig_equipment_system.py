import core.equipment_system
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0102_alter_worldconfig_death_mode_destroy_all"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldconfig",
            name="equipment_system",
            field=models.JSONField(default=core.equipment_system.default_equipment_system),
        ),
    ]
