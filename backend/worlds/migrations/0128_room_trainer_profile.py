import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0257_trainer_profiles"),
        ("worlds", "0127_room_merchant_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="room",
            name="trainer_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rooms",
                to="builders.trainerprofile",
            ),
        ),
    ]
