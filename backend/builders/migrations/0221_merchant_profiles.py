from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0220_stats_attributes_naming"),
        ("worlds", "0097_clean_slate_world_stat_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="MerchantProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("slug", models.SlugField(blank=True, max_length=120)),
                ("name", models.TextField(default="Unnamed Merchant")),
                ("notes", models.TextField(blank=True, null=True)),
                ("sell_markup", models.FloatField(default=1.0)),
                ("buy_multiplier", models.FloatField(default=0.4)),
                ("restock_interval_seconds", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "funds_mode",
                    models.TextField(
                        choices=[("unlimited", "Unlimited"), ("finite", "Finite")],
                        default="unlimited",
                    ),
                ),
                ("purchase_budget", models.PositiveIntegerField(default=0)),
                ("buyback_enabled", models.BooleanField(default=False)),
                ("buyback_max_items", models.PositiveIntegerField(default=0)),
                (
                    "buyback_expires",
                    models.TextField(
                        choices=[("on_restock", "On_restock")],
                        default="on_restock",
                    ),
                ),
                (
                    "funds_currency",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="merchant_funds_profiles",
                        to="builders.currency",
                    ),
                ),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="merchant_profiles",
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
            name="MerchantStockSlot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("key", models.SlugField(max_length=120)),
                ("count", models.PositiveIntegerField(default=1)),
                (
                    "refresh",
                    models.TextField(
                        choices=[
                            ("fill_missing", "Fill_missing"),
                            ("reroll_on_restock", "Reroll_on_restock"),
                        ],
                        default="fill_missing",
                    ),
                ),
                (
                    "item_bundle",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="merchant_stock_slots",
                        to="builders.itembundle",
                    ),
                ),
                (
                    "item_definition",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="merchant_stock_slots",
                        to="builders.itemdefinition",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stock_slots",
                        to="builders.merchantprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["created_ts", "id"],
                "unique_together": {("profile", "key")},
            },
        ),
        migrations.AddField(
            model_name="mobdefinition",
            name="attackable",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="mobdefinition",
            name="merchant_availability",
            field=models.TextField(blank=True, default="present"),
        ),
        migrations.AddField(
            model_name="mobdefinition",
            name="merchant_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="mob_definitions",
                to="builders.merchantprofile",
            ),
        ),
    ]
