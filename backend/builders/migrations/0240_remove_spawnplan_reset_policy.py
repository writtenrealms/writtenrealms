from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0239_abilitydefinition_primary_action_phases"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="spawnplan",
            name="reset_policy",
        ),
    ]
