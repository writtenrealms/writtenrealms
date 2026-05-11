from django.db import migrations, models
import django.db.models.deletion
import django.contrib.postgres.indexes


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0213_itemtemplate_weapon_damage"),
        ("worlds", "0095_worldconfig_leveling"),
    ]

    operations = [
        migrations.CreateModel(
            name="AbilityDefinition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("slug", models.TextField()),
                ("name", models.TextField()),
                ("command_verbs", models.JSONField(default=list)),
                ("action_type", models.TextField(default="primary", db_index=True)),
                ("target", models.JSONField(default=dict)),
                ("availability", models.JSONField(default=dict)),
                ("requirements", models.JSONField(default=dict)),
                ("cost", models.JSONField(default=dict)),
                ("cooldown", models.JSONField(default=dict)),
                ("components", models.JSONField(default=list)),
                ("is_active", models.BooleanField(default=True, db_index=True)),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ability_definitions",
                        to="worlds.world",
                    ),
                ),
            ],
            options={
                "ordering": ["created_ts"],
                "unique_together": {("world_id", "slug")},
            },
        ),
        migrations.AddIndex(
            model_name="abilitydefinition",
            index=models.Index(fields=["world", "slug"], name="builders_ab_world_i_21ea11_idx"),
        ),
        migrations.AddIndex(
            model_name="abilitydefinition",
            index=models.Index(fields=["world", "is_active"], name="builders_ab_world_i_1af96b_idx"),
        ),
        migrations.AddIndex(
            model_name="abilitydefinition",
            index=django.contrib.postgres.indexes.GinIndex(fields=["command_verbs"], name="builders_ab_cmdverbs_gin"),
        ),
    ]
