from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0252_rename_itemdefinition_ground_description"),
    ]

    operations = [
        migrations.AddField(
            model_name="mobdefinition",
            name="initial_state",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="spawnentry",
            name="initial_state",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
