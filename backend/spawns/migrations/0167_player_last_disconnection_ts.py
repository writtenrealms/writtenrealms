from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spawns', '0166_rip_message'),
    ]

    operations = [
        migrations.AddField(
            model_name='player',
            name='last_disconnection_ts',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
