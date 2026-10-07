from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("builders", "0264_mobdefinition_talkable"),
    ]

    operations = [
        migrations.AddField(
            model_name="spawnentry",
            name="rewards",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
