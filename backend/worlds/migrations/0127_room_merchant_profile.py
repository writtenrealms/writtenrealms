import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0256_delete_procession"),
        ("worlds", "0126_alter_instanceparticipant_exit_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="room",
            name="merchant_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rooms",
                to="builders.merchantprofile",
            ),
        ),
    ]
