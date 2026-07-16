from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0240_remove_spawnplan_reset_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="spawnplanrun",
            name="entry_states",
            field=models.JSONField(blank=True, db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name="spawnplacement",
            name="is_retired",
            field=models.BooleanField(db_default=False, default=False),
        ),
    ]
