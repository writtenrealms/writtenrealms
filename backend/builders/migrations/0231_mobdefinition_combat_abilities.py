# Generated manually during WR2 mob ability loadout implementation.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0230_mob_traits"),
    ]

    operations = [
        migrations.AddField(
            model_name="mobdefinition",
            name="combat_abilities",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
