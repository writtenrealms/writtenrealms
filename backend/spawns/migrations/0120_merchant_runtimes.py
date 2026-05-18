from django.db import migrations, models
import django.contrib.contenttypes.fields
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0221_merchant_profiles"),
        ("spawns", "0119_stats_attributes_naming"),
        ("worlds", "0097_clean_slate_world_stat_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="mob",
            name="attackable",
            field=models.BooleanField(default=True),
        ),
        migrations.CreateModel(
            name="MerchantRuntime",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("is_active", models.BooleanField(default=True)),
                ("last_restocked_ts", models.DateTimeField(blank=True, null=True)),
                ("next_restock_ts", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("remaining_purchase_budget", models.IntegerField(blank=True, null=True)),
                (
                    "mob",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="merchant_runtime",
                        to="spawns.mob",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="merchant_runtimes",
                        to="builders.merchantprofile",
                    ),
                ),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="merchant_runtimes",
                        to="worlds.world",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="MerchantStockEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("bundle_roll_id", models.TextField(blank=True, null=True)),
                ("price", models.PositiveIntegerField(default=0)),
                (
                    "status",
                    models.TextField(
                        choices=[
                            ("available", "Available"),
                            ("sold", "Sold"),
                            ("expired", "Expired"),
                            ("retired", "Retired"),
                        ],
                        db_index=True,
                        default="available",
                    ),
                ),
                (
                    "item",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="merchant_stock_entry",
                        to="spawns.item",
                    ),
                ),
                (
                    "runtime",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stock_entries",
                        to="spawns.merchantruntime",
                    ),
                ),
                (
                    "stock_slot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="runtime_entries",
                        to="builders.merchantstockslot",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="MerchantBuybackEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("sold_price", models.PositiveIntegerField(default=0)),
                ("buyback_price", models.PositiveIntegerField(default=0)),
                (
                    "status",
                    models.TextField(
                        choices=[
                            ("active", "Active"),
                            ("expired", "Expired"),
                            ("bought_back", "Bought_back"),
                        ],
                        db_index=True,
                        default="active",
                    ),
                ),
                (
                    "item",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="merchant_buyback_entry",
                        to="spawns.item",
                    ),
                ),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="merchant_buyback_entries",
                        to="spawns.player",
                    ),
                ),
                (
                    "runtime",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="buyback_entries",
                        to="spawns.merchantruntime",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="merchantruntime",
            index=models.Index(fields=["is_active"], name="spawns_merc_is_acti_b52214_idx"),
        ),
        migrations.AddIndex(
            model_name="merchantruntime",
            index=models.Index(fields=["next_restock_ts"], name="spawns_merc_next_re_c8c461_idx"),
        ),
        migrations.AddIndex(
            model_name="merchantstockentry",
            index=models.Index(fields=["status"], name="spawns_merc_status_1b18bf_idx"),
        ),
        migrations.AddIndex(
            model_name="merchantstockentry",
            index=models.Index(fields=["bundle_roll_id"], name="spawns_merc_bundle__606b91_idx"),
        ),
        migrations.AddIndex(
            model_name="merchantbuybackentry",
            index=models.Index(fields=["status"], name="spawns_merc_status_985e44_idx"),
        ),
        migrations.AddIndex(
            model_name="merchantbuybackentry",
            index=models.Index(fields=["created_ts"], name="spawns_merc_created_f04798_idx"),
        ),
    ]
