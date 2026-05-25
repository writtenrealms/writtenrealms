from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("worlds", "0100_stat_system_stats_naming"),
    ]

    operations = [
        migrations.AlterField(
            model_name="worldconfig",
            name="default_gender",
            field=models.TextField(
                choices=[
                    ("male", "Male"),
                    ("female", "Female"),
                    ("non_binary", "Non_binary"),
                ],
                default="male",
            ),
        ),
    ]
