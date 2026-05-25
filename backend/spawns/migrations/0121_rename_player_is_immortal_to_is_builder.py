from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0120_merchant_runtimes"),
    ]

    operations = [
        migrations.RenameField(
            model_name="player",
            old_name="is_immortal",
            new_name="is_builder",
        ),
    ]
