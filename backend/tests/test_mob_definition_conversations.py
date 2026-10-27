import copy
from io import StringIO
from unittest.mock import patch

import yaml
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from builders.models import MobDefinition, MobConversationPolicy
from builders.manifests import mob_definition_to_manifest
from builders.world_export import _serialize_mob_definition_manifest, manifest_stream_to_yaml, serialize_world_export_payload
from spawns.conversations.policy import set_enabled, revision_for
from tests.base import WorldTestCase
from worlds.models import World


DIALOGUE = {'greeting': 'What makes a fair bargain?', 'knowledge': 'You are a former stonecutter.',
            'when': {'always': True}}


class TestMobDefinitionConversations(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)

    def apply(self, spec, world=None):
        return self.client.post(reverse('builder-world-manifest-apply', args=[(world or self.world).pk]),
            {'manifest': manifest_stream_to_yaml([{'kind': 'mobdefinition',
                'metadata': {'slug': 'menon', 'name': 'Menon'}, 'spec': spec}])}, format='json')

    def test_import_export_detail_and_cross_world_round_trip(self):
        response = self.apply({'type': 'humanoid', 'conversation': DIALOGUE})
        self.assertIn(response.status_code, (200, 201), response.data)
        definition = MobDefinition.objects.get(world=self.world, slug='menon')
        self.assertEqual(definition.conversation, DIALOGUE)
        detail = self.client.get(reverse('builder-mob-definition-detail', args=[self.world.pk, definition.pk]))
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['conversation'], DIALOGUE)
        self.assertEqual(yaml.safe_load(detail.data['yaml'])['spec']['conversation'], DIALOGUE)
        self.world.conversation_knowledge = 'Shared world knowledge'
        self.world.save(update_fields=['conversation_knowledge'])
        set_enabled(self.world.pk, True)
        manifest = _serialize_mob_definition_manifest(definition)
        self.assertEqual(manifest['spec']['conversation'], DIALOGUE)
        self.assertNotIn('setting', manifest['spec']['conversation'])
        self.assertNotIn('enabled', manifest['spec']['conversation'])
        exported = serialize_world_export_payload(self.world)['documents']
        self.assertEqual(next(d for d in exported if d['kind'] == 'mobdefinition')['spec']['conversation'], DIALOGUE)
        target = World.objects.new_world(name='Another world', author=self.user)
        response = self.apply(manifest['spec'], target)
        self.assertIn(response.status_code, (200, 201), response.data)
        imported = MobDefinition.objects.get(world=target, slug='menon')
        self.assertEqual(imported.conversation, DIALOGUE)
        self.assertFalse(MobConversationPolicy.objects.filter(world=target).exists())

    def test_omission_preserves_null_clears_and_empty_mapping_selects_defaults(self):
        self.apply({'type': 'humanoid', 'conversation': DIALOGUE})
        definition = MobDefinition.objects.get(world=self.world, slug='menon')
        self.apply({'description': 'Stone dust covers his hands.'})
        definition.refresh_from_db()
        self.assertEqual(definition.conversation, DIALOGUE)
        for value in (None, {}):
            response = self.apply({'conversation': value})
            self.assertIn(response.status_code, (200, 201), response.data)
            definition.refresh_from_db()
            self.assertEqual(definition.conversation, value)
            self.assertEqual(mob_definition_to_manifest(definition)['spec']['conversation'], value)
        self.apply({'conversation': {'greeting': 'A new question.'}})
        definition.refresh_from_db()
        self.assertEqual(definition.conversation, {'greeting': 'A new question.'})

    def test_invalid_dialogue_is_rejected_atomically(self):
        self.apply({'type': 'humanoid', 'conversation': DIALOGUE})
        for invalid in (False, [], 'hello', {'api_key': 'unsupported'}, {'greeting': 'x' * 121},
                        {'knowledge': 'x' * 401}, {'greeting': '<script>'}, {'knowledge': True},
                        {'when': []}, {'when': {'unknown_operator': True}},
                        {'when': {'always': float('nan')}}):
            with self.subTest(invalid=invalid):
                response = self.apply({'conversation': invalid})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(MobDefinition.objects.get(world=self.world, slug='menon').conversation, DIALOGUE)
        definition = MobDefinition.objects.get(world=self.world, slug='menon')
        definition.conversation = {'greeting': 'x' * 121}
        with self.assertRaises(ValidationError):
            definition.clean()

    def test_dialogue_edits_do_not_resync_spawned_stats_or_affect_other_definitions(self):
        self.apply({'type': 'humanoid', 'conversation': DIALOGUE})
        definition = MobDefinition.objects.get(world=self.world, slug='menon')
        mob = definition.spawn(self.room, self.spawn_world)
        policy = set_enabled(self.world.pk, True)
        old_revision = revision_for(mob, policy)
        with patch('builders.mob_definitions.sync_spawned_mobs_from_definition') as sync:
            self.apply({'conversation': {'knowledge': 'You have learned something new.'}})
            definition.conversation = {}
            definition.save(update_fields=['conversation'])
        sync.assert_not_called()
        mob.refresh_from_db()
        self.assertNotEqual(revision_for(mob, policy), old_revision)
        revision = revision_for(mob, policy)
        other = MobDefinition.objects.create(world=self.world, slug='other', conversation={})
        other.conversation = DIALOGUE
        other.save(update_fields=['conversation'])
        self.assertEqual(revision_for(mob, policy), revision)

    def test_world_command_only_changes_enablement(self):
        self.apply({'type': 'humanoid', 'conversation': DIALOGUE})
        self.world.conversation_knowledge = 'Shared knowledge'
        self.world.save(update_fields=['conversation_knowledge'])
        set_enabled(self.world.pk, True)
        call_command('mob_conversations', 'disable', world_id=self.world.pk, stdout=StringIO())
        policy = MobConversationPolicy.objects.get(world=self.world)
        self.assertFalse(policy.enabled)
        self.world.refresh_from_db()
        self.assertEqual(self.world.conversation_knowledge, 'Shared knowledge')
        output = StringIO()
        call_command('mob_conversations', 'status', world_id=self.world.pk, stdout=output)
        self.assertIn('menon', output.getvalue())
        self.assertEqual(MobDefinition.objects.get(world=self.world, slug='menon').conversation, DIALOGUE)


@override_settings(MIGRATION_MODULES={})
class DialogueMigrationTestCase(TransactionTestCase):
    def setUp(self):
        super().setUp()
        recorder = MigrationRecorder(connection)
        self.had_table = recorder.has_table()
        recorder.ensure_schema()
        self.original = set(recorder.migration_qs.values_list('app', 'name'))
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes(), fake=True)

    def tearDown(self):
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            recorder = MigrationRecorder(connection)
            for app, name in set(recorder.migration_qs.values_list('app', 'name')) - self.original:
                recorder.record_unapplied(app, name)
            if not self.had_table:
                with connection.schema_editor() as editor:
                    editor.delete_model(recorder.Migration)
            super().tearDown()

    def migrate(self, name):
        executor = MigrationExecutor(connection)
        executor.migrate([('builders', name)])
        executor = MigrationExecutor(connection)
        return executor.loader.project_state(list(executor.loader.applied_migrations)).apps

class TestMobDialogueMigration(DialogueMigrationTestCase):
    def test_existing_dialogue_and_disabled_world_settings_survive_forward_and_reverse(self):
        old = self.migrate('0269_mob_conversations')
        world = old.get_model('worlds', 'World').objects.create(name='Existing world')
        definitions = old.get_model('builders', 'MobDefinition')
        definition = definitions.objects.create(world_id=world.pk, slug='menon')
        definitions.objects.create(world_id=world.pk, slug='defaults')
        definitions.objects.create(world_id=world.pk, slug='unconfigured')
        entry = copy.deepcopy(DIALOGUE)
        policy = old.get_model('builders', 'MobConversationPolicy').objects.create(world_id=world.pk,
            enabled=False, setting='Shared setting', mobs={'menon': entry, 'defaults': {}})
        new = self.migrate('0270_mob_definition_conversation')
        migrated = new.get_model('builders', 'MobDefinition').objects
        self.assertEqual(migrated.get(pk=definition.pk).conversation, entry)
        self.assertEqual(migrated.get(slug='defaults').conversation, {})
        self.assertIsNone(migrated.get(slug='unconfigured').conversation)
        current = new.get_model('builders', 'MobConversationPolicy').objects.get(pk=world.pk)
        self.assertEqual(current.setting, 'Shared setting')
        self.assertFalse(current.enabled)
        self.assertNotEqual(current.revision, policy.revision)
        old = self.migrate('0269_mob_conversations')
        restored = old.get_model('builders', 'MobConversationPolicy').objects.get(pk=world.pk)
        self.assertEqual(restored.mobs, {'menon': entry, 'defaults': {}})
        self.assertFalse(restored.enabled)
        self.assertEqual(restored.setting, 'Shared setting')
