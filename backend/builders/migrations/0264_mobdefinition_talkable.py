from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('builders', '0263_rip_message'),
    ]

    operations = [
        migrations.AddField(
            model_name='mobdefinition',
            name='talkable',
            field=models.BooleanField(default=True),
        ),
    ]
