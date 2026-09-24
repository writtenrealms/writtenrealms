from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('worlds', '0135_lobby_leaderboards')]

    operations = [
        migrations.AddField(
            model_name='worldconfig', name='entry_message',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='worldconfig', name='entry_message_mode',
            field=models.CharField(
                max_length=10, default='all',
                choices=[('all', 'all'), ('reveal', 'reveal'), ('replace', 'replace')],
            ),
        ),
    ]
