from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('quests', '0005_rename_quests_ques_player__8dbf78_idx_quests_ques_player__b99121_idx_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='questinstance',
            index=models.Index(
                fields=['player', 'template', 'status', 'resolved_at'],
                name='quests_qi_player_template_log',
            ),
        ),
    ]
