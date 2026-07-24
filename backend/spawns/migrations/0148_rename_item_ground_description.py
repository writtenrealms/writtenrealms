from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0252_rename_itemdefinition_ground_description"),
        ("spawns", "0147_scheduled_trigger_runs"),
    ]

    operations = [
        migrations.RenameField(
            model_name="item",
            old_name="ground_description",
            new_name="room_description",
        ),
    ]
