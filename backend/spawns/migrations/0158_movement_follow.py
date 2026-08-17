import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0157_room_merchant_runtimes"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="follow_move_sequence",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mob",
            name="follow_move_sequence",
            field=models.BigIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="MovementFollow",
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
                (
                    "last_processed_sequence",
                    models.BigIntegerField(default=0),
                ),
                (
                    "follower",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="movement_follow",
                        to="spawns.player",
                    ),
                ),
                (
                    "leader_mob",
                    models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="movement_followers",
                        to="spawns.mob",
                    ),
                ),
                (
                    "leader_player",
                    models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="movement_followers",
                        to="spawns.player",
                    ),
                ),
            ],
            options={
                "ordering": ["created_ts"],
                "indexes": [
                    models.Index(
                        fields=["leader_player", "id"],
                        condition=models.Q(leader_player__isnull=False),
                        name="spawn_follow_player_page",
                    ),
                    models.Index(
                        fields=["leader_mob", "id"],
                        condition=models.Q(leader_mob__isnull=False),
                        name="spawn_follow_mob_page",
                    ),
                    models.Index(
                        fields=["leader_player", "last_processed_sequence"],
                        condition=models.Q(leader_player__isnull=False),
                        name="spawn_follow_player_seq",
                    ),
                    models.Index(
                        fields=["leader_mob", "last_processed_sequence"],
                        condition=models.Q(leader_mob__isnull=False),
                        name="spawn_follow_mob_seq",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                leader_player__isnull=False,
                                leader_mob__isnull=True,
                            )
                            | models.Q(
                                leader_player__isnull=True,
                                leader_mob__isnull=False,
                            )
                        ),
                        name="spawns_follow_exactly_one_leader",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(leader_player__isnull=True)
                            | ~models.Q(
                                leader_player=models.F("follower")
                            )
                        ),
                        name="spawns_follow_no_self_player",
                    ),
                ],
            },
        ),
    ]
