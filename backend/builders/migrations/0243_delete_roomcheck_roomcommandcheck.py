from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0242_backfill_spawn_plan_entry_states"),
        ("spawns", "0140_delete_roomcommandcheckstate"),
    ]

    operations = [
        migrations.DeleteModel(
            name="RoomCheck",
        ),
        migrations.DeleteModel(
            name="RoomCommandCheck",
        ),
    ]
