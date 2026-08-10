import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0156_scheduled_trigger_request_context"),
        ("worlds", "0127_room_merchant_profile"),
    ]

    operations = [
        migrations.AlterField(
            model_name="merchantruntime",
            name="mob",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="merchant_runtime",
                to="spawns.mob",
            ),
        ),
        migrations.AddField(
            model_name="merchantruntime",
            name="room",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="merchant_runtimes",
                to="worlds.room",
            ),
        ),
        migrations.AddConstraint(
            model_name="merchantruntime",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(mob__isnull=False, room__isnull=True)
                    | models.Q(mob__isnull=True, room__isnull=False)
                ),
                name="spawns_merchant_exactly_one_host",
            ),
        ),
        migrations.AddConstraint(
            model_name="merchantruntime",
            constraint=models.UniqueConstraint(
                condition=models.Q(room__isnull=False),
                fields=("world", "room"),
                name="spawns_merchant_runtime_world_room",
            ),
        ),
    ]
