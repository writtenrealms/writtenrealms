from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0215_alter_skill_unique_together_remove_skill_consumes_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemtemplate",
            name="input_attributes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="mobtemplate",
            name="input_attributes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RemoveField(
            model_name="itemtemplate",
            name="strength",
        ),
        migrations.RemoveField(
            model_name="itemtemplate",
            name="constitution",
        ),
        migrations.RemoveField(
            model_name="itemtemplate",
            name="dexterity",
        ),
        migrations.RemoveField(
            model_name="itemtemplate",
            name="intelligence",
        ),
    ]
