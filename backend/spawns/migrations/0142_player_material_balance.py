from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('builders', '0245_crafting'),
        ('spawns', '0141_rename_spawns_merc_status_985e44_idx_spawns_merc_status_3eb40a_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CraftingActionReceipt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_ts', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('modified_ts', models.DateTimeField(auto_now=True, db_index=True)),
                ('request_id', models.UUIDField()),
                ('segment', models.CharField(default='r', max_length=128)),
                ('action', models.SlugField(max_length=32)),
                ('result', models.JSONField(default=dict)),
                ('player', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='crafting_action_receipts', to='spawns.player')),
            ],
            options={
                'ordering': ['created_ts'],
                'indexes': [models.Index(fields=['player', 'request_id'], name='spawns_crafting_receipt_idx')],
                'constraints': [models.UniqueConstraint(fields=('player', 'request_id', 'segment'), name='spawns_crafting_receipt_unique')],
            },
        ),
        migrations.CreateModel(
            name='PlayerMaterialBalance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_ts', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('modified_ts', models.DateTimeField(auto_now=True, db_index=True)),
                ('quantity', models.PositiveIntegerField(default=0)),
                ('material', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='player_balances', to='builders.craftmaterial')),
                ('player', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='material_balances', to='spawns.player')),
            ],
            options={
                'ordering': ['created_ts'],
                'constraints': [
                    models.UniqueConstraint(fields=('player', 'material'), name='spawns_material_balance_unique'),
                    models.CheckConstraint(condition=models.Q(('quantity__gte', 0)), name='spawns_material_balance_nonnegative'),
                ],
            },
        ),
    ]
