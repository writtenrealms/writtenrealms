from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("quests", "0002_phase2_runtime"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="questjournalentry",
            name="lead",
        ),
        migrations.RemoveField(
            model_name="questjournalentry",
            name="stakes",
        ),
    ]
