from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0232_faction_type_and_assignment_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="abilitydefinition",
            name="consumes_primary_action",
            field=models.BooleanField(default=True),
        ),
    ]
