from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0155_door_actions"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduledtriggerrun",
            name="request_connection_id",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="scheduledtriggerrun",
            name="request_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="scheduledtriggerrun",
            name="request_segment",
            field=models.CharField(default="r", max_length=128),
        ),
    ]
