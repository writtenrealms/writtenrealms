import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('worlds', '0117_worldconfig_announce_duel_results_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='instanceparticipant',
            name='exit_reason',
            field=models.TextField(
                blank=True,
                choices=[
                    ('left', 'Left'),
                    ('replaced', 'Replaced'),
                    ('death_delegated', 'Death_delegated'),
                ],
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='instanceparticipant',
            name='return_runtime_world',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name='returning_instance_participants',
                to='worlds.world',
            ),
        ),
        migrations.AddConstraint(
            model_name='instanceparticipant',
            constraint=models.UniqueConstraint(
                condition=models.Q(('exited_at__isnull', True)),
                fields=('player',),
                name='worlds_instance_one_active_player',
            ),
        ),
    ]
