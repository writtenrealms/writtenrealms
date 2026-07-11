from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0238_delete_legacy_template_models"),
    ]

    operations = [
        migrations.RenameField(
            model_name="abilitydefinition",
            old_name="consumes_primary_action",
            new_name="consumes_primary_action_on_resolve",
        ),
        migrations.AddField(
            model_name="abilitydefinition",
            name="consumes_primary_action_while_casting",
            field=models.BooleanField(default=True),
        ),
    ]
