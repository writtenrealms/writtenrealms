import uuid

from django.db import migrations, models


def assign_trigger_uids(apps, schema_editor):
    Trigger = apps.get_model('builders', 'Trigger')
    triggers = Trigger.objects.using(schema_editor.connection.alias)
    batch = []
    for trigger in triggers.filter(uid__isnull=True).only('id').iterator(chunk_size=1000):
        trigger.uid = uuid.uuid4()
        batch.append(trigger)
        if len(batch) == 1000:
            triggers.bulk_update(batch, ['uid'], batch_size=1000)
            batch = []
    if batch:
        triggers.bulk_update(batch, ['uid'], batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [
        ('builders', '0261_participant_combat_integrity'),
    ]

    operations = [
        migrations.AddField(
            model_name='trigger',
            name='uid',
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(assign_trigger_uids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='trigger',
            name='uid',
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        migrations.AddConstraint(
            model_name='trigger',
            constraint=models.UniqueConstraint(
                fields=('world', 'uid'), name='trigger_world_uid_unique',
            ),
        ),
    ]
