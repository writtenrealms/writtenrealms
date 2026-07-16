from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0241_spawn_plan_live_edit_state"),
        ("spawns", "0139_player_live_name_index"),
    ]

    operations = [
        migrations.DeleteModel(
            name="RoomCommandCheckState",
        ),
    ]
