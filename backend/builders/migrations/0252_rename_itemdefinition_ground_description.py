from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0251_trigger_steps_and_error_policy"),
    ]

    operations = [
        migrations.RenameField(
            model_name="itemdefinition",
            old_name="ground_description",
            new_name="room_description",
        ),
    ]
