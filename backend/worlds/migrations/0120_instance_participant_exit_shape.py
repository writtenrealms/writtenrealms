from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('worlds', '0119_deterministic_death_routing'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='instanceparticipant',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        exited_at__isnull=True,
                        exit_reason__isnull=True,
                        return_runtime_world__isnull=False,
                    )
                    | models.Q(
                        exited_at__isnull=False,
                        exit_reason__isnull=False,
                        return_runtime_world__isnull=True,
                    )
                ),
                name='worlds_instance_participant_exit_shape',
            ),
        ),
    ]
