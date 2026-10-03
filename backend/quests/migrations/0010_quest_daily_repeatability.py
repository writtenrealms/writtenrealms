from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('quests', '0009_scope_instance_quest_offers'),
    ]

    operations = [
        migrations.AddField(
            model_name='questtemplate',
            name='repeatability_reset_at',
            field=models.CharField(blank=True, default='', max_length=5),
        ),
        migrations.AddField(
            model_name='questtemplate',
            name='repeatability_timezone',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AlterField(
            model_name='questtemplate',
            name='repeatability_mode',
            field=models.TextField(
                choices=[('never', 'Never'), ('cooldown', 'Cooldown'),
                         ('daily', 'Daily'), ('always', 'Always')],
                default='never',
            ),
        ),
    ]
