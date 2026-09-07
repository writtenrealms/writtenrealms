import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('worlds', '0130_zone_default_roam_chance'),
    ]

    operations = [
        migrations.AddField(
            model_name='worldconfig',
            name='instance_single_player',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='worldconfig',
            name='instance_time_control',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='single_player',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='owner',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='owned_instance_runs',
                to='spawns.player',
            ),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='time_control',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='time_mode',
            field=models.TextField(
                choices=[('automatic', 'Automatic'), ('manual', 'Manual')],
                default='automatic',
            ),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='time_interval',
            field=models.FloatField(
                default=2,
                validators=[
                    django.core.validators.MinValueValidator(0.25),
                    django.core.validators.MaxValueValidator(3600),
                ],
            ),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='simulation_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='simulation_tick',
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='time_generation',
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='next_tick_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='pending_command',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='instancerun',
            name='pending_revision',
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name='instancerun',
            index=models.Index(
                fields=['time_control', 'status', 'next_tick_at'],
                name='worlds_run_clock_due_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='instancerun',
            index=models.Index(
                fields=['template_world', 'owner', 'status'],
                name='worlds_run_owner_status_idx',
            ),
        ),
        migrations.AddConstraint(
            model_name='instancerun',
            constraint=models.CheckConstraint(
                condition=models.Q(time_control=False) | models.Q(single_player=True),
                name='worlds_run_time_control_solo',
            ),
        ),
        migrations.AddConstraint(
            model_name='worldconfig',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(instance_time_control=False)
                    | models.Q(instance_single_player=True)
                ),
                name='worlds_config_time_control_solo',
            ),
        ),
    ]
