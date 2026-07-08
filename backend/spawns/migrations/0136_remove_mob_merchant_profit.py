from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('spawns', '0135_remove_runtime_template_rule_fks'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='mob',
            name='merchant_profit',
        ),
    ]
