from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0158_movement_follow"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameeventoutbox",
            name="depends_on_batch_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddIndex(
            model_name="gameeventoutbox",
            index=models.Index(
                condition=models.Q(
                    ("depends_on_batch_id__isnull", True),
                    ("sequence", 0),
                ),
                fields=["available_ts", "created_ts", "batch_id", "id"],
                name="spawn_event_due_ready",
            ),
        ),
    ]
