from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('system', '0024_sitecontrol_platform_policy')]

    operations = [
        migrations.AddField(
            model_name='sitecontrol',
            name='main_world_id',
            field=models.PositiveIntegerField(
                default=1, null=True, blank=True, validators=[MinValueValidator(1)],
                help_text=(
                    'Root world opened by the homepage and lobby. Defaults to world 1. '
                    'Leave blank to show the multi-world homepage and lobby. '
                    'This does not change world visibility or access permissions.'
                ),
            ),
        ),
    ]
