from io import StringIO

import yaml
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from builders.instance_templates import create_instance_template
from builders.manifests import world_config_to_manifest
from builders.models import MobConversationPolicy, MobDefinition
from builders.world_export import serialize_world_export_payload
from spawns.conversations.policy import knowledge_packet, policy_queryset, revision_for, set_enabled
from tests.base import WorldTestCase
from tests.test_mob_definition_conversations import DialogueMigrationTestCase
from worlds.models import World


class TestWorldDefinitionConversations(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def apply(self, spec, world=None):
        return self.client.post(reverse('builder-world-manifest-apply', args=[(world or self.world).pk]),
            {'manifest': yaml.safe_dump({'kind': 'world', 'spec': spec})}, format='json')

    def test_world_editor_export_and_cross_world_round_trip_without_enablement(self):
        value = {'knowledge': 'Local people trade grain and pottery.'}
        response = self.apply({'conversation': value})
        self.assertEqual(response.status_code, 200, response.data)
        self.world.refresh_from_db()
        self.assertEqual(self.world.conversation_knowledge, value['knowledge'])
        response = self.client.get(reverse('builder-world-config', args=[self.world.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(yaml.safe_load(response.data['yaml'])['spec']['conversation'], value)
        self.assertEqual(response.data['world']['conversation_knowledge'], value['knowledge'])
        set_enabled(self.world.pk, True)
        exported = serialize_world_export_payload(self.world)['documents']
        spec = next(d['spec'] for d in exported if d['kind'] == 'world')
        self.assertEqual(spec['conversation'], value)
        self.assertNotIn('enabled', spec['conversation'])
        target = World.objects.new_world(name='Another world', author=self.user)
        response = self.apply({'conversation': spec['conversation']}, target)
        self.assertEqual(response.status_code, 200, response.data)
        target.refresh_from_db()
        self.assertEqual(target.conversation_knowledge, value['knowledge'])
        self.assertFalse(MobConversationPolicy.objects.filter(world=target).exists())

    def test_omission_preserves_and_clear_round_trips(self):
        for clear in (None, {}, {'knowledge': ''}):
            self.apply({'conversation': {'knowledge': 'Shared knowledge'}})
            self.apply({'motd': 'Welcome'})
            self.world.refresh_from_db()
            self.assertEqual(self.world.conversation_knowledge, 'Shared knowledge')
            response = self.apply({'conversation': clear})
            self.assertEqual(response.status_code, 200, response.data)
            self.world.refresh_from_db()
            spec = world_config_to_manifest(world=self.world)['spec']
            self.assertEqual(spec['conversation'], {'knowledge': ''})
            self.apply({'conversation': {'knowledge': 'Other knowledge'}})
            self.apply({'conversation': spec['conversation']})
            self.world.refresh_from_db()
            self.assertEqual(self.world.conversation_knowledge, '')

    def test_validation_is_atomic_and_enablement_is_not_authored(self):
        self.apply({'conversation': {'knowledge': 'Original'}})
        for invalid in (False, [], 'text', {'knowledge': None}, {'knowledge': 4},
                        {'knowledge': 'x' * 801}, {'setting': 'legacy'}, {'enabled': True}, {'api_key': 'invalid'}):
            with self.subTest(invalid=invalid):
                response = self.apply({'conversation': invalid, 'motd': 'Must not save'})
                self.assertEqual(response.status_code, 400, response.data)
                self.world.refresh_from_db()
                self.assertEqual(self.world.conversation_knowledge, 'Original')
                self.assertNotEqual(self.world.motd, 'Must not save')
        self.world.conversation_knowledge = 'x' * 801
        with self.assertRaises(ValidationError):
            self.world.full_clean()

    def test_runtime_reads_base_definition_and_world_edits_invalidate_focus_without_extra_queries(self):
        self.apply({'conversation': {'knowledge': 'Current base knowledge'}})
        definition = MobDefinition.objects.create(world=self.world, slug='potter', conversation={})
        mob = definition.spawn(self.room, self.spawn_world)
        set_enabled(self.world.pk, True)
        policy = policy_queryset().get(world=self.world)
        with self.assertNumQueries(0):
            previous = revision_for(mob, policy)
            packet = knowledge_packet(self.player, mob, policy)
        self.assertEqual(packet['common_local_knowledge'], 'Current base knowledge')
        self.apply({'conversation': {'knowledge': 'Edited knowledge'}})
        policy = policy_queryset().get(world=self.world)
        self.assertNotEqual(revision_for(mob, policy), previous)
        self.assertTrue(policy.enabled)
        self.assertEqual(knowledge_packet(self.player, mob, policy)['common_local_knowledge'], 'Edited knowledge')
        self.spawn_world.refresh_from_db()
        self.assertEqual(self.spawn_world.conversation_knowledge, '')
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        instance = create_instance_template(base_world=self.world, author=self.user, name='A cave')
        self.assertNotIn('conversation', world_config_to_manifest(world=instance)['spec'])
        self.assertEqual(self.apply({'conversation': {'knowledge': 'Override'}}, instance).status_code, 400)

    def test_enable_disable_command_never_changes_knowledge(self):
        self.apply({'conversation': {'knowledge': 'Authored knowledge'}})
        for action, enabled in [('enable', True), ('disable', False), ('enable', True)]:
            call_command('mob_conversations', action, world_id=self.world.pk, stdout=StringIO())
            self.assertEqual(MobConversationPolicy.objects.get(world=self.world).enabled, enabled)
            self.world.refresh_from_db()
            self.assertEqual(self.world.conversation_knowledge, 'Authored knowledge')


class TestWorldDialogueMigration(DialogueMigrationTestCase):
    def test_forward_and_reverse_preserve_knowledge_enablement_and_new_unconfigured_worlds(self):
        old = self.migrate('0270_mob_definition_conversation')
        world = old.get_model('worlds', 'World').objects.create(name='Existing world')
        policy = old.get_model('builders', 'MobConversationPolicy').objects.create(world_id=world.pk,
            enabled=False, setting='Exact shared knowledge')
        new = self.migrate('0271_world_definition_conversation')
        self.assertEqual(new.get_model('worlds', 'World').objects.get(pk=world.pk).conversation_knowledge, 'Exact shared knowledge')
        current = new.get_model('builders', 'MobConversationPolicy').objects.get(pk=world.pk)
        self.assertFalse(current.enabled)
        self.assertNotEqual(current.revision, policy.revision)
        extra = new.get_model('worlds', 'World').objects.create(name='New world', conversation_knowledge='New knowledge')
        old = self.migrate('0270_mob_definition_conversation')
        restored = old.get_model('builders', 'MobConversationPolicy').objects.get(pk=world.pk)
        self.assertEqual(restored.setting, 'Exact shared knowledge')
        self.assertFalse(restored.enabled)
        restored_extra = old.get_model('builders', 'MobConversationPolicy').objects.get(pk=extra.pk)
        self.assertEqual(restored_extra.setting, 'New knowledge')
        self.assertFalse(restored_extra.enabled)
