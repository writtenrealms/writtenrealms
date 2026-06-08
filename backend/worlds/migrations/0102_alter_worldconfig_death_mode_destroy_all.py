from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('worlds', '0101_default_gender_male'),
    ]

    operations = [
        migrations.AlterField(
            model_name='worldconfig',
            name='death_mode',
            field=models.TextField(
                choices=[
                    ('lose_all', 'Lose_all'),
                    ('lose_none', 'Lose_none'),
                    ('lose_eq', 'Lose_eq'),
                    ('destroy_eq', 'Destroy_eq'),
                    ('destroy_all', 'Destroy_all'),
                    ('lose_gold', 'Lose_gold'),
                    ('lose_inv', 'Lose_inv'),
                ],
                default='lose_none',
            ),
        ),
    ]
