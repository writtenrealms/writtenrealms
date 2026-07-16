from django.db import migrations


def canonicalize_legacy_allow_pvp(apps, schema_editor):
    """Make the richer pvp_mode authoritative before dropping the old flag."""
    WorldConfig = apps.get_model("worlds", "WorldConfig")
    WorldConfig.objects.filter(
        pvp_mode="disabled",
        allow_pvp=True,
    ).update(allow_pvp=False)
    WorldConfig.objects.exclude(
        pvp_mode="disabled",
    ).filter(allow_pvp=False).update(allow_pvp=True)


class Migration(migrations.Migration):
    dependencies = [
        ("worlds", "0111_add_weapon_damage_to_stat_display"),
    ]

    operations = [
        migrations.RunPython(
            canonicalize_legacy_allow_pvp,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="worldconfig",
            name="allow_pvp",
        ),
    ]
