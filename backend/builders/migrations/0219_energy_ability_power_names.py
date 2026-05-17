from django.db import migrations


def rename_food_type_value(apps, schema_editor):
    item_template = apps.get_model("builders", "ItemTemplate")
    item_template.objects.filter(food_type="mana").update(food_type="energy")


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0218_mob_definitions"),
    ]

    operations = [
        migrations.RenameField(
            model_name="itemtemplate",
            old_name="mana_max",
            new_name="energy_max",
        ),
        migrations.RenameField(
            model_name="itemtemplate",
            old_name="mana_regen",
            new_name="energy_regen",
        ),
        migrations.RenameField(
            model_name="itemtemplate",
            old_name="spell_power",
            new_name="ability_power",
        ),
        migrations.RenameField(
            model_name="mobtemplate",
            old_name="mana",
            new_name="energy",
        ),
        migrations.RenameField(
            model_name="mobtemplate",
            old_name="mana_max",
            new_name="energy_max",
        ),
        migrations.RenameField(
            model_name="mobtemplate",
            old_name="mana_regen",
            new_name="energy_regen",
        ),
        migrations.RenameField(
            model_name="mobtemplate",
            old_name="spell_power",
            new_name="ability_power",
        ),
        migrations.RunPython(rename_food_type_value, migrations.RunPython.noop),
    ]
