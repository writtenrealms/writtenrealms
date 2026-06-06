from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0219_energy_ability_power_names"),
    ]

    operations = [
        migrations.RenameField(
            model_name="itemtemplate",
            old_name="input_attributes",
            new_name="attributes",
        ),
        migrations.RenameField(
            model_name="itemdefinition",
            old_name="base_input_attributes",
            new_name="attributes",
        ),
        migrations.RenameField(
            model_name="mobtemplate",
            old_name="input_attributes",
            new_name="attributes",
        ),
        migrations.RenameField(
            model_name="mobdefinition",
            old_name="base_input_attributes",
            new_name="attributes",
        ),
    ]
