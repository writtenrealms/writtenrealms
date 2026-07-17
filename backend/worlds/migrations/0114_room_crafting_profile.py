from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('builders', '0245_crafting'),
        ('worlds', '0113_rename_worlds_inst_player__289221_idx_worlds_inst_player__f8ff2e_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='room',
            name='crafting_profile',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='rooms',
                to='builders.craftingprofile',
            ),
        ),
    ]
