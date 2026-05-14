from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0114_remove_playerflexskill_player_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="input_attributes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="mob",
            name="input_attributes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="player",
            name="input_attributes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RemoveField(
            model_name="item",
            name="strength",
        ),
        migrations.RemoveField(
            model_name="item",
            name="constitution",
        ),
        migrations.RemoveField(
            model_name="item",
            name="dexterity",
        ),
        migrations.RemoveField(
            model_name="item",
            name="intelligence",
        ),
    ]
