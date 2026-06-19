# Generated manually during WR2 mob trait implementation.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0229_spawn_plans"),
    ]

    operations = [
        migrations.AddField(
            model_name="mobdefinition",
            name="traits",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RenameField(
            model_name="spawnentry",
            old_name="affixes",
            new_name="traits",
        ),
        migrations.RenameField(
            model_name="spawnplacement",
            old_name="affixes",
            new_name="traits",
        ),
    ]
