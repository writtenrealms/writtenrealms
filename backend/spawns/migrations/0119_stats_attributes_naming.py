from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0118_energy_ability_power_names"),
    ]

    operations = [
        migrations.RenameField(
            model_name="player",
            old_name="input_attributes",
            new_name="attributes",
        ),
        migrations.RenameField(
            model_name="mob",
            old_name="input_attributes",
            new_name="attributes",
        ),
        migrations.RenameField(
            model_name="item",
            old_name="input_attributes",
            new_name="attributes",
        ),
    ]
