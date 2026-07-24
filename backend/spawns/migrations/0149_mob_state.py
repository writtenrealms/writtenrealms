import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0148_rename_item_ground_description"),
    ]

    operations = [
        migrations.CreateModel(
            name="MobState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_ts",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "modified_ts",
                    models.DateTimeField(auto_now=True, db_index=True),
                ),
                ("data", models.JSONField(default=dict)),
                ("version", models.BigIntegerField(default=0)),
                (
                    "mob",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="character_state_record",
                        to="spawns.mob",
                    ),
                ),
            ],
            options={
                "ordering": ["mob_id"],
                "abstract": False,
            },
        ),
    ]
