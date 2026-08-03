from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0255_spawn_entry_relational_targets"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="faction",
            name="death_rooms",
        ),
        migrations.DeleteModel(
            name="Procession",
        ),
    ]
