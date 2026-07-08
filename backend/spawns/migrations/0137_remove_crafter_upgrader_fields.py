from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('spawns', '0136_remove_mob_merchant_profit'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='item',
            name='upgrade_count',
        ),
        migrations.RemoveField(
            model_name='mob',
            name='craft_multiplier',
        ),
        migrations.RemoveField(
            model_name='mob',
            name='is_crafter',
        ),
        migrations.RemoveField(
            model_name='mob',
            name='is_upgrader',
        ),
        migrations.RemoveField(
            model_name='mob',
            name='upgrade_cost_multiplier',
        ),
    ]
