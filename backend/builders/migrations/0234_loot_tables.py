from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0233_abilitydefinition_consumes_primary_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="mobdefinition",
            name="loot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="spawnentry",
            name="loot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
