from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.reverse import reverse

from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.world_export import manifest_stream_to_yaml, serialize_world_export_payload
from spawns.state_payloads import serialize_world
from tests.base import WorldTestCase
from tests.combat_fixtures import dispatch_and_drain_combat
from tests.utils import capture_game_messages
from worlds.models import World


class WorldEntryMessageTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        create_currency(world=self.world, code='obol', name='Obol')
        self.client.force_authenticate(self.user)
        for world in (self.world, self.spawn_world):
            world.is_multiplayer = True
            world.save(update_fields=['is_multiplayer'])
        self.template = create_instance_template(
            base_world=self.world, author=self.user, name='Cave', instance_slug='cave',
        )

    def apply(self, world, spec):
        return self.client.post(
            reverse('builder-world-manifest-apply', args=[world.pk]),
            {'manifest': manifest_stream_to_yaml([{'kind': 'world', 'spec': spec}])}, format='json',
        )

    def configure(self, world, text, mode='all'):
        world.config.entry_message = text
        world.config.entry_message_mode = mode
        world.config.save(update_fields=['entry_message', 'entry_message_mode'])

    def test_defaults_disable_messages_for_base_and_instance(self):
        for world in (self.world, self.template):
            runtime = world.spawned_worlds.first() or world.create_spawn_world()
            payload = serialize_world(runtime)
            self.assertEqual(payload['entry_message'], '')
            self.assertEqual(payload['entry_message_mode'], 'all')

    def test_all_modes_can_be_authored_and_read_for_both_scopes(self):
        for world in (self.world, self.template):
            for mode in ('all', 'reveal', 'replace'):
                with self.subTest(world=world.pk, mode=mode):
                    text = 'The cave is silent.\n\nA light appears.'
                    response = self.apply(world, {'entry_message': text, 'entry_message_mode': mode})
                    self.assertEqual(response.status_code, 200, response.data)
                    response = self.client.get(reverse('builder-world-config', args=[world.pk]))
                    self.assertEqual(response.status_code, 200, response.data)
                    for payload in (response.data['config'], response.data['manifest']['spec']):
                        self.assertEqual(payload['entry_message'], text)
                        self.assertEqual(payload['entry_message_mode'], mode)

    def test_new_instances_do_not_copy_the_base_introduction(self):
        self.configure(self.world, 'Welcome to the island.', 'reveal')
        template = create_instance_template(base_world=self.world, author=self.user, name='Another Cave')
        self.assertEqual(template.config.entry_message, '')
        self.assertEqual(template.config.entry_message_mode, 'all')

    def test_partial_edits_preserve_messages_and_empty_text_disables_them(self):
        self.configure(self.world, 'Keep me.', 'replace')
        response = self.apply(self.world, {'name': 'Renamed'})
        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.entry_message, 'Keep me.')
        self.assertEqual(self.world.config.entry_message_mode, 'replace')
        response = self.apply(self.world, {'entry_message': ''})
        self.assertEqual(response.status_code, 200, response.data)
        self.world.config.refresh_from_db()
        self.assertEqual(self.world.config.entry_message, '')

    def test_invalid_modes_fail_atomically(self):
        self.configure(self.world, 'Keep me.')
        for mode in ('invalid', '', None, [], True):
            response = self.apply(self.world, {'entry_message': 'Changed', 'entry_message_mode': mode})
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('entry_message_mode', str(response.data))
            self.world.config.refresh_from_db()
            self.assertEqual(self.world.config.entry_message, 'Keep me.')

    def test_family_export_import_preserves_independent_messages(self):
        self.configure(self.world, 'The island awaits.', 'reveal')
        self.configure(self.template, 'You enter the cave.\n\nIt is dark.', 'replace')
        documents = serialize_world_export_payload(self.world)['documents']
        target = World.objects.new_world(name='Imported', author=self.user, is_multiplayer=True)
        response = self.client.post(
            reverse('builder-world-manifest-apply', args=[target.pk]),
            {'manifest': manifest_stream_to_yaml(documents)}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        target.refresh_from_db()
        imported = World.objects.get(instance_of=target, instance_slug='cave')
        self.assertEqual(target.config.entry_message, 'The island awaits.')
        self.assertEqual(target.config.entry_message_mode, 'reveal')
        self.assertEqual(imported.config.entry_message, 'You enter the cave.\n\nIt is dark.')
        self.assertEqual(imported.config.entry_message_mode, 'replace')
        self.assertEqual(serialize_world_export_payload(target)['documents'], documents)

    def test_instance_entry_and_exit_snapshots_use_destination_message(self):
        self.configure(self.world, 'Welcome to the island.', 'all')
        self.configure(self.template, 'Inside the cave.\n\nA light appears.', 'reveal')
        self.room.transfer_to = self.template.config.starting_room
        self.room.save(update_fields=['transfer_to'])
        for command, expected, mode in (
            ('enter', 'Inside the cave.\n\nA light appears.', 'reveal'),
            ('leave', 'Welcome to the island.', 'all'),
        ):
            with capture_game_messages() as messages:
                dispatch_and_drain_combat(self.player.pk, command)
            snapshots = [row['message'] for row in messages
                         if row['message']['type'] == 'cmd.state.sync.success']
            self.assertTrue(snapshots, messages)
            self.assertEqual(snapshots[-1]['data']['world']['entry_message'], expected)
            self.assertEqual(snapshots[-1]['data']['world']['entry_message_mode'], mode)

    def test_entry_messages_add_no_queries_to_world_serialization(self):
        # Both configurations use the same snapshot query path; paragraph timing
        # never creates server tasks, queries, or extra events.
        counts = []
        for text in ('', 'One.\n\nTwo.\n\nThree.'):
            self.configure(self.world, text, 'reveal')
            runtime = World.objects.get(pk=self.spawn_world.pk)
            with CaptureQueriesContext(connection) as queries:
                payload = serialize_world(runtime)
            counts.append(len(queries))
            self.assertEqual(payload['entry_message'], text)
        self.assertEqual(counts[0], counts[1])
