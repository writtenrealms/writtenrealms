import uuid
from django.db import migrations


def move_knowledge(apps, schema_editor):
    Policy = apps.get_model('builders', 'MobConversationPolicy')
    World = apps.get_model('worlds', 'World')
    alias = schema_editor.connection.alias
    for policy in Policy.objects.using(alias).iterator(chunk_size=100):
        World.objects.using(alias).filter(pk=policy.world_id).update(conversation_knowledge=policy.setting)
        Policy.objects.using(alias).filter(pk=policy.pk).update(revision=uuid.uuid4())


def restore_knowledge(apps, schema_editor):
    Policy = apps.get_model('builders', 'MobConversationPolicy')
    World = apps.get_model('worlds', 'World')
    alias = schema_editor.connection.alias
    for world in World.objects.using(alias).filter(context__isnull=True, instance_of__isnull=True).iterator(chunk_size=100):
        existing = Policy.objects.using(alias).filter(world_id=world.pk)
        values = {'setting': world.conversation_knowledge, 'revision': uuid.uuid4()}
        if not existing.update(**values) and world.conversation_knowledge:
            Policy.objects.using(alias).create(world_id=world.pk, enabled=False, **values)


class Migration(migrations.Migration):
    dependencies = [('builders', '0270_mob_definition_conversation'), ('worlds', '0142_world_conversation_knowledge')]
    operations = [
        migrations.RunPython(move_knowledge, restore_knowledge),
        migrations.RemoveField(model_name='mobconversationpolicy', name='setting'),
    ]
