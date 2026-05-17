from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0217_item_definitions"),
        ("worlds", "0097_clean_slate_world_stat_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="MobDefinition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("slug", models.SlugField(blank=True, max_length=120)),
                ("name", models.TextField(default="Unnamed Mob")),
                ("description", models.TextField(blank=True, null=True)),
                ("room_description", models.TextField(blank=True, null=True)),
                ("keywords", models.TextField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "mob_type",
                    models.TextField(
                        choices=[
                            ("aberration", "Aberration"),
                            ("beast", "Beast"),
                            ("celestial", "Celestial"),
                            ("construct", "Construct"),
                            ("dragon", "Dragon"),
                            ("elemental", "Elemental"),
                            ("fey", "Fey"),
                            ("fiend", "Fiend"),
                            ("giant", "Giant"),
                            ("humanoid", "Humanoid"),
                            ("monstrosity", "Monstrosity"),
                            ("ooze", "Ooze"),
                            ("plant", "Plant"),
                            ("undead", "Undead"),
                        ],
                        default="beast",
                    ),
                ),
                ("assists", models.BooleanField(default=False)),
                ("base_properties", models.JSONField(blank=True, default=dict)),
                ("base_input_attributes", models.JSONField(blank=True, default=dict)),
                ("randomization", models.JSONField(blank=True, default=dict)),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mob_definitions",
                        to="worlds.world",
                    ),
                ),
            ],
            options={
                "ordering": ["created_ts"],
                "unique_together": {("world", "slug")},
            },
        ),
    ]
