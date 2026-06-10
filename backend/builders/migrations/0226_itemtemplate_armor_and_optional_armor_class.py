from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0225_trigger_after_death_room_enter"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemtemplate",
            name="armor",
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="itemtemplate",
            name="armor_class",
            field=models.TextField(blank=True, null=True),
        ),
    ]
