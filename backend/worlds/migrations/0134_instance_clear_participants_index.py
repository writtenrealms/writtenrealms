from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('worlds', '0133_instance_completion_goals')]

    operations = [
        migrations.AddIndex(
            model_name='instanceclearrecord',
            index=GinIndex(
                fields=['participants'],
                opclasses=['jsonb_path_ops'],
                name='worlds_clear_participants_gin',
            ),
        ),
    ]
