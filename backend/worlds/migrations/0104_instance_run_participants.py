import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0130_mob_ability_runtime"),
        ("worlds", "0103_worldconfig_equipment_system"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstanceRun",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("ref", models.TextField(db_index=True)),
                ("status", models.TextField(choices=[("created", "Created"), ("active", "Active"), ("resolving", "Resolving"), ("completed", "Completed"), ("failed", "Failed"), ("expired", "Expired"), ("abandoned", "Abandoned"), ("closed", "Closed"), ("cleaned", "Cleaned")], db_index=True, default="active")),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("last_active_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("failed_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("cleanup_after", models.DateTimeField(blank=True, null=True)),
                ("goal_spec", models.JSONField(blank=True, default=dict)),
                ("progress", models.JSONField(blank=True, default=dict)),
                ("outcome", models.JSONField(blank=True, default=dict)),
                ("seed", models.TextField(blank=True)),
                ("initial_member_ids", models.JSONField(blank=True, default=list)),
                ("base_world", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="instance_runs_as_base", to="worlds.world")),
                ("leader", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="led_instance_runs", to="spawns.player")),
                ("spawned_world", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="instance_run", to="worlds.world")),
                ("template_world", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="instance_runs_as_template", to="worlds.world")),
            ],
            options={
                "ordering": ["created_ts"],
            },
        ),
        migrations.CreateModel(
            name="InstanceParticipant",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("role", models.TextField(choices=[("leader", "Leader"), ("member", "Member")], db_index=True, default="member")),
                ("joined_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("exited_at", models.DateTimeField(blank=True, null=True)),
                ("player", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="instance_participations", to="spawns.player")),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="participants", to="worlds.instancerun")),
                ("transfer_from", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="instance_participants_from", to="worlds.room")),
            ],
            options={
                "ordering": ["created_ts"],
                "unique_together": {("run", "player")},
            },
        ),
        migrations.AddIndex(
            model_name="instancerun",
            index=models.Index(fields=["status"], name="worlds_inst_status_d92411_idx"),
        ),
        migrations.AddIndex(
            model_name="instancerun",
            index=models.Index(fields=["ref"], name="worlds_inst_ref_9f3c6d_idx"),
        ),
        migrations.AddIndex(
            model_name="instancerun",
            index=models.Index(fields=["base_world", "status"], name="worlds_inst_base_wo_a61353_idx"),
        ),
        migrations.AddIndex(
            model_name="instancerun",
            index=models.Index(fields=["template_world", "status"], name="worlds_inst_templat_95f85d_idx"),
        ),
        migrations.AddIndex(
            model_name="instanceparticipant",
            index=models.Index(fields=["player", "exited_at"], name="worlds_inst_player__289221_idx"),
        ),
        migrations.AddIndex(
            model_name="instanceparticipant",
            index=models.Index(fields=["run", "exited_at"], name="worlds_inst_run_id_337547_idx"),
        ),
        migrations.AddIndex(
            model_name="instanceparticipant",
            index=models.Index(fields=["role"], name="worlds_inst_role_011083_idx"),
        ),
    ]
