from django.db import migrations, models


OLD_DEFAULT_STAT_KEYS = {
    "primary_attributes",
}


def clear_wr1_default_stat_systems(apps, schema_editor):
    WorldConfig = apps.get_model("worlds", "WorldConfig")
    for config in WorldConfig.objects.all().iterator():
        stat_system = config.stat_system
        should_clear = False
        if isinstance(stat_system, dict):
            should_clear = bool(OLD_DEFAULT_STAT_KEYS & set(stat_system.keys()))
            mob_boost = (
                stat_system.get("formulas", {})
                .get("mob_boost", {})
                if isinstance(stat_system.get("formulas"), dict)
                else {}
            )
            if isinstance(mob_boost, dict) and "constitution_share" in mob_boost:
                should_clear = True
        if should_clear:
            config.stat_system = {}
            config.is_classless = True
            config.save(update_fields=["stat_system", "is_classless"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0096_worldconfig_ability_progression"),
    ]

    operations = [
        migrations.AlterField(
            model_name="worldconfig",
            name="is_classless",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(clear_wr1_default_stat_systems, noop),
    ]
