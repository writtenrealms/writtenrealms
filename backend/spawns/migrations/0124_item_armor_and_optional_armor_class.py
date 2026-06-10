from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0123_player_command_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="armor",
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="item",
            name="armor_class",
            field=models.TextField(blank=True, null=True),
        ),
    ]
