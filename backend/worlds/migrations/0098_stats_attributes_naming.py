from django.db import migrations


def rename_stat_system_attributes(apps, schema_editor):
    world_config = apps.get_model("worlds", "WorldConfig")
    for config in world_config.objects.exclude(stat_system={}):
        stat_system = config.stat_system
        if not isinstance(stat_system, dict):
            continue

        changed = False
        if "primary_attributes" in stat_system:
            if "attributes" not in stat_system:
                stat_system["attributes"] = stat_system["primary_attributes"]
            stat_system.pop("primary_attributes", None)
            changed = True

        if "input_attributes" in stat_system:
            if "attributes" not in stat_system:
                stat_system["attributes"] = stat_system["input_attributes"]
            stat_system.pop("input_attributes", None)
            changed = True

        labels = stat_system.get("labels")
        if isinstance(labels, dict) and "input_attributes" in labels:
            if "attributes" not in labels:
                labels["attributes"] = labels["input_attributes"]
            labels.pop("input_attributes", None)
            changed = True

        label_order = labels.get("order") if isinstance(labels, dict) else None
        if isinstance(label_order, dict) and "input_attributes" in label_order:
            if "attributes" not in label_order:
                label_order["attributes"] = label_order["input_attributes"]
            label_order.pop("input_attributes", None)
            changed = True

        def rename_profile_fields(profile):
            nonlocal changed
            if not isinstance(profile, dict):
                return
            if "primary_attribute" in profile:
                if "main_attribute" not in profile:
                    profile["main_attribute"] = profile["primary_attribute"]
                profile.pop("primary_attribute", None)
                changed = True
            if "base_attribute_weights" in profile:
                if "attribute_weights" not in profile:
                    profile["attribute_weights"] = profile["base_attribute_weights"]
                profile.pop("base_attribute_weights", None)
                changed = True

        rename_profile_fields(stat_system.get("default_profile"))
        class_profiles = stat_system.get("class_profiles")
        if isinstance(class_profiles, dict):
            for profile in class_profiles.values():
                rename_profile_fields(profile)

        if changed:
            config.stat_system = stat_system
            config.save(update_fields=["stat_system"])


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0097_clean_slate_world_stat_config"),
    ]

    operations = [
        migrations.RunPython(rename_stat_system_attributes, migrations.RunPython.noop),
    ]
