import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('builders', '0253_mob_initial_state'),
        ('worlds', '0118_instance_participant_return_runtime'),
    ]

    operations = [
        migrations.AlterField(
            model_name='worldconfig',
            name='death_room',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='death_room_for',
                to='worlds.room',
            ),
        ),
        migrations.AddField(
            model_name='worldconfig',
            name='death_routing_generation',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='worldconfig',
            name='death_routing_source',
            field=models.TextField(
                choices=[
                    ('local', 'Local'),
                    ('base_world', 'Base_world'),
                ],
                default='local',
            ),
        ),
        migrations.AddField(
            model_name='worldconfig',
            name='death_routing_source_generation',
            field=models.BigIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='DeathRoutingPolicy',
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
                ('enabled', models.BooleanField(default=False)),
                (
                    'config',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='death_routing_policy',
                        to='worlds.worldconfig',
                    ),
                ),
            ],
            options={'ordering': ['created_ts']},
        ),
        migrations.CreateModel(
            name='DeathRoutingCompiledSnapshot',
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
                ('plan_generation', models.BigIntegerField()),
                ('cache_version', models.PositiveSmallIntegerField(default=2)),
                ('data', models.JSONField(default=dict)),
                ('retirement_pending', models.BooleanField(default=False)),
                ('retired_at', models.DateTimeField(blank=True, null=True)),
                (
                    'config',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='death_routing_snapshots',
                        to='worlds.worldconfig',
                    ),
                ),
            ],
            options={
                'ordering': ['created_ts'],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('config', 'plan_generation', 'cache_version'),
                        name='worlds_death_route_snapshot_unique',
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name='DeathRoutingRoute',
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
                ('position', models.PositiveSmallIntegerField()),
                ('condition', models.JSONField(default=dict)),
                ('compiled_version', models.PositiveSmallIntegerField(default=2)),
                ('compiled_condition', models.JSONField(default=dict)),
                (
                    'destination_room',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name='death_routing_routes',
                        to='worlds.room',
                    ),
                ),
                (
                    'policy',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='routes',
                        to='worlds.deathroutingpolicy',
                    ),
                ),
            ],
            options={
                'ordering': ['position', 'id'],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('policy', 'position'),
                        name='worlds_death_route_position_unique',
                    ),
                    models.CheckConstraint(
                        condition=models.Q(position__lt=32),
                        name='worlds_death_route_position_bound',
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name='DeathRoutingSnapshotReference',
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
                (
                    'core_faction',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name='death_routing_snapshot_references',
                        to='builders.faction',
                    ),
                ),
                (
                    'destination_room',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name='death_routing_snapshot_references',
                        to='worlds.room',
                    ),
                ),
                (
                    'origin_zone',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name='death_routing_snapshot_references',
                        to='worlds.zone',
                    ),
                ),
                (
                    'snapshot',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='references',
                        to='worlds.deathroutingcompiledsnapshot',
                    ),
                ),
            ],
            options={
                'ordering': ['created_ts'],
                'constraints': [
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                destination_room__isnull=False,
                                core_faction__isnull=True,
                                origin_zone__isnull=True,
                            )
                            | models.Q(
                                destination_room__isnull=True,
                                core_faction__isnull=False,
                                origin_zone__isnull=True,
                            )
                            | models.Q(
                                destination_room__isnull=True,
                                core_faction__isnull=True,
                                origin_zone__isnull=False,
                            )
                        ),
                        name='worlds_death_snapshot_ref_one_target',
                    ),
                    models.UniqueConstraint(
                        fields=('snapshot', 'destination_room'),
                        condition=models.Q(destination_room__isnull=False),
                        name='worlds_death_snapshot_room_unique',
                    ),
                    models.UniqueConstraint(
                        fields=('snapshot', 'core_faction'),
                        condition=models.Q(core_faction__isnull=False),
                        name='worlds_death_snapshot_faction_unique',
                    ),
                    models.UniqueConstraint(
                        fields=('snapshot', 'origin_zone'),
                        condition=models.Q(origin_zone__isnull=False),
                        name='worlds_death_snapshot_zone_unique',
                    ),
                ],
            },
        ),
    ]
