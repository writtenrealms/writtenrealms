from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('worlds', '0140_worldinformation')]

    operations = [
        migrations.AddField(
            model_name='worldconfig',
            name='entry_message_conditions',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
