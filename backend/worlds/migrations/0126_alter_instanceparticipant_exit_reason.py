from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('worlds', '0125_instance_template_manifest_slugs'),
    ]

    operations = [
        migrations.AlterField(
            model_name='instanceparticipant',
            name='exit_reason',
            field=models.TextField(
                blank=True,
                choices=[
                    ('left', 'Left'),
                    ('forced', 'Forced'),
                    ('replaced', 'Replaced'),
                    ('death_delegated', 'Death_delegated'),
                ],
                null=True,
            ),
        ),
    ]
