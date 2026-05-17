from django.db import migrations


def rename_food_type_value(apps, schema_editor):
    item = apps.get_model("spawns", "Item")
    item.objects.filter(food_type="mana").update(food_type="energy")


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0117_mob_definition_link"),
    ]

    operations = [
        migrations.RenameField(
            model_name="player",
            old_name="mana",
            new_name="energy",
        ),
        migrations.RenameField(
            model_name="mob",
            old_name="mana",
            new_name="energy",
        ),
        migrations.RenameField(
            model_name="mob",
            old_name="mana_max",
            new_name="energy_max",
        ),
        migrations.RenameField(
            model_name="mob",
            old_name="mana_regen",
            new_name="energy_regen",
        ),
        migrations.RenameField(
            model_name="mob",
            old_name="spell_power",
            new_name="ability_power",
        ),
        migrations.RenameField(
            model_name="item",
            old_name="mana_max",
            new_name="energy_max",
        ),
        migrations.RenameField(
            model_name="item",
            old_name="mana_regen",
            new_name="energy_regen",
        ),
        migrations.RenameField(
            model_name="item",
            old_name="spell_power",
            new_name="ability_power",
        ),
        migrations.RunPython(rename_food_type_value, migrations.RunPython.noop),
    ]
