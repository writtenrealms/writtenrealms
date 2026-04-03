from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('worlds', '0090_state_only_worldconfig_starting_eq'),
        ('spawns', '0107_player_effects'),
        ('quests', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='QuestInstance',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_ts', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('modified_ts', models.DateTimeField(auto_now=True, db_index=True)),
                ('status', models.TextField(choices=[('active', 'Active'), ('resolved', 'Resolved')], default='active')),
                ('resolution', models.TextField(blank=True, choices=[('complete', 'Complete'), ('abandoned', 'Abandoned')], null=True)),
                ('current_step_id', models.TextField(blank=True, null=True)),
                ('slot_bindings', models.JSONField(default=dict)),
                ('local_state', models.JSONField(default=dict)),
                ('visible_objective_ids', models.JSONField(default=list)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('last_journal_entry_at', models.DateTimeField(blank=True, null=True)),
                ('player', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='quest_instances', to='spawns.player')),
                ('template', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='instances', to='quests.questtemplate')),
                ('world', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='quest_instances', to='worlds.world')),
            ],
            options={
                'ordering': ['-modified_ts', '-created_ts'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='QuestJournalEntry',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_ts', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('modified_ts', models.DateTimeField(auto_now=True, db_index=True)),
                ('step_id', models.TextField(blank=True, null=True)),
                ('entry_type', models.TextField(choices=[('step_entered', 'Step_entered'), ('objective_updated', 'Objective_updated'), ('resolved', 'Resolved'), ('system', 'System')], default='step_entered')),
                ('recap', models.TextField(blank=True, null=True)),
                ('lead', models.TextField(blank=True, null=True)),
                ('stakes', models.TextField(blank=True, null=True)),
                ('payload', models.JSONField(default=dict)),
                ('quest_instance', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='journal_entries', to='quests.questinstance')),
            ],
            options={
                'ordering': ['created_ts'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='QuestObjectiveState',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_ts', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('modified_ts', models.DateTimeField(auto_now=True, db_index=True)),
                ('objective_id', models.TextField()),
                ('text', models.TextField(blank=True, null=True)),
                ('status', models.TextField(choices=[('active', 'Active'), ('complete', 'Complete'), ('failed', 'Failed'), ('hidden', 'Hidden')], default='active')),
                ('progress_current', models.PositiveIntegerField(default=0)),
                ('progress_target', models.PositiveIntegerField(default=1)),
                ('distinct_values', models.JSONField(default=list)),
                ('last_matching_event_type', models.TextField(blank=True, null=True)),
                ('last_matching_event_at', models.DateTimeField(blank=True, null=True)),
                ('deadline_at', models.DateTimeField(blank=True, null=True)),
                ('quest_instance', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='objective_states', to='quests.questinstance')),
            ],
            options={
                'ordering': ['created_ts'],
                'abstract': False,
                'unique_together': {('quest_instance', 'objective_id')},
            },
        ),
        migrations.CreateModel(
            name='QuestOfferState',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_ts', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('modified_ts', models.DateTimeField(auto_now=True, db_index=True)),
                ('is_visible', models.BooleanField(default=False)),
                ('last_seen_at', models.DateTimeField(blank=True, null=True)),
                ('last_accepted_at', models.DateTimeField(blank=True, null=True)),
                ('last_resolved_at', models.DateTimeField(blank=True, null=True)),
                ('cooldown_until', models.DateTimeField(blank=True, null=True)),
                ('snoozed_until', models.DateTimeField(blank=True, null=True)),
                ('dismiss_count', models.PositiveIntegerField(default=0)),
                ('player', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='quest_offer_states', to='spawns.player')),
                ('template', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='offer_states', to='quests.questtemplate')),
            ],
            options={
                'ordering': ['-modified_ts', '-created_ts'],
                'abstract': False,
                'unique_together': {('player', 'template')},
            },
        ),
        migrations.AddIndex(
            model_name='questinstance',
            index=models.Index(fields=['player', 'status'], name='quests_ques_player__8dbf78_idx'),
        ),
        migrations.AddIndex(
            model_name='questinstance',
            index=models.Index(fields=['template', 'status'], name='quests_ques_templat_ac9a47_idx'),
        ),
        migrations.AddIndex(
            model_name='questinstance',
            index=models.Index(fields=['world', 'status'], name='quests_ques_world_i_4f4a2a_idx'),
        ),
    ]
