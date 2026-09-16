import core.leaderboards
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('worlds', '0134_instance_clear_participants_index')]

    operations = [
        migrations.AddField(
            model_name='worldconfig', name='leaderboards',
            field=models.JSONField(blank=True, default=core.leaderboards.default_leaderboards),
        ),
        migrations.AddField(model_name='instanceclearrecord', name='single_player', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='instanceclearrecord', name='ranking_eligible', field=models.BooleanField(default=False)),
        migrations.AddField(
            model_name='instanceclearrecord', name='ranking_player',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='ranked_instance_clears', to='spawns.player'),
        ),
        migrations.AddIndex(
            model_name='instanceclearrecord',
            index=models.Index(fields=['template_world', 'time_control', 'single_player', 'ranking_player', 'clear_time_ms'],
                               condition=models.Q(ranking_eligible=True), name='worlds_clear_personal_best_idx'),
        ),
    ]
