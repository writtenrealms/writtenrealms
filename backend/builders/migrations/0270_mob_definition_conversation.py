import uuid

from django.db import migrations, models


def move_dialogue_to_definitions(apps, schema_editor):
    Policy = apps.get_model('builders', 'MobConversationPolicy')
    Definition = apps.get_model('builders', 'MobDefinition')
    alias = schema_editor.connection.alias
    for policy in Policy.objects.using(alias).iterator(chunk_size=100):
        entries = policy.mobs or {}
        for definition in Definition.objects.using(alias).filter(world_id=policy.world_id, slug__in=entries):
            Definition.objects.using(alias).filter(pk=definition.pk).update(conversation=entries[definition.slug])
        # Existing focus/queued replies must not outlive the authoring transition.
        Policy.objects.using(alias).filter(pk=policy.pk).update(revision=uuid.uuid4())


def restore_policy_entries(apps, schema_editor):
    Policy = apps.get_model('builders', 'MobConversationPolicy')
    Definition = apps.get_model('builders', 'MobDefinition')
    alias = schema_editor.connection.alias
    for world_id in Definition.objects.using(alias).filter(conversation__isnull=False).values_list('world_id', flat=True).distinct():
        entries = dict(Definition.objects.using(alias).filter(world_id=world_id, conversation__isnull=False)
                       .values_list('slug', 'conversation'))
        Policy.objects.using(alias).update_or_create(world_id=world_id,
            defaults={'mobs': entries, 'revision': uuid.uuid4()})


class Migration(migrations.Migration):
    dependencies = [('builders', '0269_mob_conversations')]
    operations = [
        migrations.AddField(model_name='mobdefinition', name='conversation',
                            field=models.JSONField(blank=True, default=None, null=True)),
        migrations.RunPython(move_dialogue_to_definitions, restore_policy_entries),
        migrations.RemoveField(model_name='mobconversationpolicy', name='mobs'),
    ]
