from django.db import migrations


def rename_profile_fields(profile):
    if not isinstance(profile, dict):
        return False

    changed = False
    if "derived_rules" in profile:
        if "stat_rules" not in profile:
            profile["stat_rules"] = profile["derived_rules"]
        profile.pop("derived_rules", None)
        changed = True
    return changed


def rename_stat_system_fields(apps, schema_editor):
    WorldConfig = apps.get_model("worlds", "WorldConfig")
    for config in WorldConfig.objects.exclude(stat_system={}):
        stat_system = config.stat_system
        if not isinstance(stat_system, dict):
            continue

        changed = False
        labels = stat_system.get("labels")
        if isinstance(labels, dict) and "derived" in labels:
            if "stats" not in labels:
                labels["stats"] = labels["derived"]
            labels.pop("derived", None)
            changed = True

        label_order = labels.get("order") if isinstance(labels, dict) else None
        if isinstance(label_order, dict) and "derived" in label_order:
            if "stats" not in label_order:
                label_order["stats"] = label_order["derived"]
            label_order.pop("derived", None)
            changed = True

        if "derived_display_order" in stat_system:
            if "stat_display_order" not in stat_system:
                stat_system["stat_display_order"] = stat_system["derived_display_order"]
            stat_system.pop("derived_display_order", None)
            changed = True

        changed = rename_profile_fields(stat_system.get("default_profile")) or changed
        class_profiles = stat_system.get("class_profiles")
        if isinstance(class_profiles, dict):
            for profile in class_profiles.values():
                changed = rename_profile_fields(profile) or changed

        if changed:
            config.stat_system = stat_system
            config.save(update_fields=["stat_system"])


class Migration(migrations.Migration):
    dependencies = [
        ("worlds", "0099_alter_worldconfig_stat_system"),
    ]

    operations = [
        migrations.RunPython(rename_stat_system_fields, migrations.RunPython.noop),
    ]
