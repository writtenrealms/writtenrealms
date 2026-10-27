import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('worlds', '0141_worldconfig_entry_message_conditions')]
    operations = [migrations.AddField(
        model_name='world', name='conversation_knowledge',
        field=models.TextField(blank=True, default='', validators=[django.core.validators.MaxLengthValidator(800)]),
    )]
