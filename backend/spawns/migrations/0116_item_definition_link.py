from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0217_item_definitions"),
        ("spawns", "0115_dynamic_input_attributes"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="definition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="spawned_items",
                to="builders.itemdefinition",
            ),
        ),
        migrations.AddField(
            model_name="item",
            name="definition_slug_snapshot",
            field=models.SlugField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="item",
            name="roll_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
