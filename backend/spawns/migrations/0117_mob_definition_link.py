from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0218_mob_definitions"),
        ("spawns", "0116_item_definition_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="mob",
            name="definition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="spawned_mobs",
                to="builders.mobdefinition",
            ),
        ),
        migrations.AddField(
            model_name="mob",
            name="definition_slug_snapshot",
            field=models.SlugField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="mob",
            name="roll_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
