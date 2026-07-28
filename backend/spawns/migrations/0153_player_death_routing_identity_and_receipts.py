import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('builders', '0253_mob_initial_state'),
        ('spawns', '0152_combatparticipant_spawn_combat_player_active'),
        ('worlds', '0119_deterministic_death_routing'),
    ]

    operations = [
        migrations.AddField(
            model_name='player',
            name='core_faction',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='core_faction_players',
                to='builders.faction',
            ),
        ),
        migrations.AddField(
            model_name='player',
            name='death_sequence',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='player',
            name='location_sequence',
            field=models.BigIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='DeathResolutionReceipt',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('created_ts', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('modified_ts', models.DateTimeField(auto_now=True, db_index=True)),
                ('death_token', models.UUIDField()),
                ('request_fingerprint', models.CharField(max_length=128)),
                ('routing_source', models.CharField(max_length=32)),
                ('source_generation', models.BigIntegerField(default=0)),
                ('plan_generation', models.BigIntegerField(default=0)),
                (
                    'matched_route_position',
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                ('decision_reason', models.CharField(max_length=64)),
                (
                    'fallback_reason',
                    models.CharField(blank=True, default='', max_length=64),
                ),
                ('death_sequence', models.BigIntegerField()),
                ('location_sequence', models.BigIntegerField()),
                ('penalty', models.JSONField(blank=True, default=dict)),
                ('corpse_id', models.BigIntegerField(blank=True, null=True)),
                ('result', models.JSONField(blank=True, default=dict)),
                (
                    'core_faction',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='death_resolution_receipts',
                        to='builders.faction',
                    ),
                ),
                (
                    'destination_room',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='death_receipts_ending_here',
                        to='worlds.room',
                    ),
                ),
                (
                    'destination_world',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='death_receipts_ending_here',
                        to='worlds.world',
                    ),
                ),
                (
                    'origin_config',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='origin_death_resolution_receipts',
                        to='worlds.worldconfig',
                    ),
                ),
                (
                    'origin_instance_participant',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='death_resolution_receipts',
                        to='worlds.instanceparticipant',
                    ),
                ),
                (
                    'origin_instance_run',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='death_resolution_receipts',
                        to='worlds.instancerun',
                    ),
                ),
                (
                    'origin_room',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='death_receipts_originating_here',
                        to='worlds.room',
                    ),
                ),
                (
                    'origin_world',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='death_receipts_originating_here',
                        to='worlds.world',
                    ),
                ),
                (
                    'plan_config',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='plan_death_resolution_receipts',
                        to='worlds.worldconfig',
                    ),
                ),
                (
                    'player',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='death_resolution_receipts',
                        to='spawns.player',
                    ),
                ),
            ],
            options={
                'ordering': ['created_ts'],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('player', 'death_token'),
                        name='spawns_death_receipt_player_token',
                    ),
                ],
            },
        ),
    ]
