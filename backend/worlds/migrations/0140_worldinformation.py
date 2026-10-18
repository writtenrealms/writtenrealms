from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('worlds', '0139_remove_worldconfig_death_route')]

    operations = [
        migrations.CreateModel(
            name='WorldInformation',
            fields=[
                ('world', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, primary_key=True,
                    serialize=False, to='worlds.world')),
                ('information', models.TextField(blank=True, default='', max_length=50000)),
            ],
        ),
    ]
