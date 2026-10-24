from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('spawns', '0171_world_upkeep')]

    operations = [
        migrations.AlterField(
            model_name='item',
            name='capacity',
            field=models.IntegerField(default=100),
        ),
    ]
