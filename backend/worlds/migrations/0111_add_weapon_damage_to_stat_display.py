from django.db import migrations


def add_weapon_damage_to_stat_display(apps, schema_editor):
    WorldConfig = apps.get_model("worlds", "WorldConfig")
    for config in WorldConfig.objects.exclude(stat_system={}):
        stat_system = config.stat_system
        if not isinstance(stat_system, dict):
            continue

        display_order = stat_system.get("stat_display_order")
        if not isinstance(display_order, list) or "weapon_damage" in display_order:
            continue

        updated_order = list(display_order)
        try:
            attack_power_index = updated_order.index("attack_power")
        except ValueError:
            attack_power_index = 0
        updated_order.insert(attack_power_index, "weapon_damage")
        stat_system["stat_display_order"] = updated_order
        config.stat_system = stat_system
        config.save(update_fields=["stat_system"])


class Migration(migrations.Migration):
    dependencies = [
        ("worlds", "0110_alter_zone_center"),
    ]

    operations = [
        migrations.RunPython(
            add_weapon_damage_to_stat_display,
            migrations.RunPython.noop,
        ),
    ]
