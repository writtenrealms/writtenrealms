from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):

    dependencies = [
        ('spawns', '0138_active_effects'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='player',
            index=models.Index(
                models.F('world'),
                Lower('name'),
                condition=models.Q(in_game=True),
                name='spawn_player_world_lname_live',
            ),
        ),
    ]
