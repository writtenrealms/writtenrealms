from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('spawns', '0163_remove_pair_combat_authority')]
    operations = [migrations.CreateModel(
        name='InstanceClockWork',
        fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('kind', models.CharField(max_length=32)),
            ('payload', models.JSONField(default=dict)),
            ('dedupe_key', models.CharField(max_length=160, unique=True)),
            ('due_at', models.DateTimeField(blank=True, db_index=True, null=True)),
            ('world', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='worlds.world')),
        ],
        options={'indexes': [models.Index(
            fields=['world', 'due_at', 'id'], name='spawns_clock_work_due_idx',
            condition=~models.Q(kind__in=['advance_receipt', 'trigger_gate']),
        )]},
    ), migrations.AddIndex(
        model_name='scheduledtriggerrun',
        index=models.Index(fields=['runtime_world', 'status', 'next_run_ts'], name='spawn_trigger_world_due_idx'),
    ), migrations.AddIndex(
        model_name='preparedgameaction',
        index=models.Index(fields=['runtime_world', 'status', 'run_at'], name='spawn_prepared_world_due_idx'),
    ), migrations.AddIndex(
        model_name='merchantruntime',
        index=models.Index(fields=['world', 'next_restock_ts'], name='spawns_shop_world_restock_idx'),
    )]
