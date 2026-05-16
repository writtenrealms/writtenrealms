from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0216_dynamic_input_attributes"),
        ("worlds", "0097_clean_slate_world_stat_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="ItemDefinition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("slug", models.SlugField(blank=True, max_length=120)),
                ("name", models.TextField(default="Unnamed Item")),
                ("description", models.TextField(blank=True, null=True)),
                ("ground_description", models.TextField(blank=True, null=True)),
                ("keywords", models.TextField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "item_type",
                    models.TextField(
                        choices=[
                            ("equippable", "Equippable"),
                            ("consumable", "Consumable"),
                            ("food", "Food"),
                            ("light", "Light"),
                            ("container", "Container"),
                            ("key", "Key"),
                            ("inert", "Inert"),
                            ("corpse", "Corpse"),
                            ("trash", "Trash"),
                            ("quest", "Quest"),
                            ("ammunition", "Ammunition"),
                            ("augment", "Augment"),
                        ],
                        default="inert",
                    ),
                ),
                ("base_properties", models.JSONField(blank=True, default=dict)),
                ("base_input_attributes", models.JSONField(blank=True, default=dict)),
                ("randomization", models.JSONField(blank=True, default=dict)),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="item_definitions",
                        to="worlds.world",
                    ),
                ),
            ],
            options={
                "ordering": ["created_ts"],
                "unique_together": {("world", "slug")},
            },
        ),
        migrations.CreateModel(
            name="ItemBundle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("slug", models.SlugField(blank=True, max_length=120)),
                ("name", models.TextField(default="Unnamed Item Bundle")),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="item_bundles",
                        to="worlds.world",
                    ),
                ),
            ],
            options={
                "ordering": ["created_ts"],
                "unique_together": {("world", "slug")},
            },
        ),
        migrations.CreateModel(
            name="ItemBundleEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("weight", models.PositiveIntegerField(default=1)),
                ("min_quantity", models.PositiveIntegerField(default=1)),
                ("max_quantity", models.PositiveIntegerField(default=1)),
                ("probability", models.PositiveIntegerField(default=100)),
                (
                    "bundle",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entries",
                        to="builders.itembundle",
                    ),
                ),
                (
                    "item_definition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bundle_entries",
                        to="builders.itemdefinition",
                    ),
                ),
            ],
            options={
                "ordering": ["created_ts", "id"],
            },
        ),
        migrations.AlterField(
            model_name="mobtemplateinventory",
            name="item_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inventory_for_mobs",
                to="builders.itemtemplate",
            ),
        ),
        migrations.AddField(
            model_name="mobtemplateinventory",
            name="item_definition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inventory_for_mobs",
                to="builders.itemdefinition",
            ),
        ),
        migrations.AddField(
            model_name="mobtemplateinventory",
            name="item_bundle",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="inventory_for_mobs",
                to="builders.itembundle",
            ),
        ),
        migrations.AddField(
            model_name="merchantinventory",
            name="item_definition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="builders.itemdefinition",
            ),
        ),
        migrations.AddField(
            model_name="merchantinventory",
            name="item_bundle",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="builders.itembundle",
            ),
        ),
    ]
