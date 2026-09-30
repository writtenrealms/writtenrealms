from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spawns', '0167_player_last_disconnection_ts'),
    ]

    operations = [
        migrations.AddField(
            model_name='mob',
            name='talkable',
            field=models.BooleanField(default=True),
        ),
    ]
