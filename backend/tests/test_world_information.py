from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.reverse import reverse

from builders.currencies import create_currency
from builders.models import WorldBuilder
from builders.world_export import manifest_stream_to_yaml
from tests.base import WorldTestCase
from worlds.information import world_information
from worlds.models import World, WorldInformation, WorldURL


class WorldInformationTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.world.is_public = True
        self.world.save(update_fields=['is_public'])
        self.endpoint = reverse('lobby-world-information', args=[self.world.pk])
        self.markdown = '## Welcome\n\nA **world** to explore.  \nA new line.\n\n    look\n'

    def save(self, information):
        return self.client.patch(self.endpoint, {'information': information}, format='json')

    def test_public_read_defaults_and_exact_markdown_round_trip(self):
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            'id': self.world.pk, 'name': self.world.name, 'built_by': '', 'information': '', 'can_edit': False,
        })
        self.assertFalse(WorldInformation.objects.exists())
        self.client.force_authenticate(self.user)
        self.assertEqual(self.save(self.markdown).status_code, 200)
        self.assertTrue(self.client.get(self.endpoint).data['can_edit'])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.endpoint).data['information'], self.markdown)

    def test_builder_credit_uses_only_the_configured_value(self):
        for credit in (None, '', 'Phalanx team', '<Builders> & friends'):
            with self.subTest(credit=credit):
                self.world_config.built_by = credit
                self.world_config.save(update_fields=['built_by'])
                response = self.client.get(self.endpoint)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['built_by'], credit or '')

    def test_only_world_editors_can_save(self):
        member = self.create_user('member@example.com')
        staff = self.create_user('staff@example.com', is_staff=True)
        outsider = self.create_user('outsider@example.com')
        other_world = World.objects.new_world(name='Other', author=self.user)
        WorldBuilder.objects.create(world=other_world, user=outsider, builder_rank=4)
        builder = WorldBuilder.objects.create(world=self.world, user=member, builder_rank=0)
        for user in (None, outsider, member):
            self.client.force_authenticate(user)
            self.assertFalse(self.client.get(self.endpoint).data['can_edit'])
            self.assertIn(self.save('Not allowed').status_code, (401, 403))
        self.assertFalse(WorldInformation.objects.exists())
        builder.builder_rank = 1
        builder.save(update_fields=['builder_rank'])
        for user in (member, staff, self.user):
            self.client.force_authenticate(user)
            self.assertTrue(self.client.get(self.endpoint).data['can_edit'])
            self.assertEqual(self.save(self.markdown).status_code, 200)

    def test_private_access_and_private_alias_match_the_lobby(self):
        self.world.is_public = False
        self.world.save(update_fields=['is_public'])
        WorldInformation.objects.create(world=self.world, information=self.markdown)
        outsider = self.create_user('outsider@example.com')
        reader = self.create_user('reader@example.com')
        self.create_player('Reader', user=reader)
        WorldBuilder.objects.create(world=self.world, user=outsider, builder_rank=0)
        self.assertIn(self.client.get(self.endpoint).status_code, (401, 403))
        for user in (reader, outsider, self.user):
            self.client.force_authenticate(user)
            response = self.client.get(self.endpoint)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['can_edit'], user == self.user)
        unrelated = self.create_user('unrelated@example.com')
        self.client.force_authenticate(unrelated)
        self.assertEqual(self.client.get(self.endpoint).status_code, 403)
        WorldURL.objects.create(world=self.world, url='secret-world', is_private=True)
        alias = reverse('lobby-world-information', args=['secret-world'])
        self.assertEqual(self.client.get(alias).status_code, 200)
        self.assertEqual(self.client.patch(alias, {'information': 'No'}, format='json').status_code, 403)

    def test_validation_clear_and_no_other_world_fields_written(self):
        self.client.force_authenticate(self.user)
        for value in (None, 42, True, [], {}, 'x' * 50001):
            with self.subTest(value_type=type(value)):
                self.assertEqual(self.save(value).status_code, 400)
        self.assertEqual(self.client.patch(self.endpoint, {}, format='json').status_code, 400)
        self.assertEqual(self.save('x' * 50000).status_code, 200)
        self.assertEqual(self.save('').status_code, 200)
        self.assertEqual(world_information(self.world), '')
        response = self.client.patch(self.endpoint, {'information': self.markdown, 'name': 'Changed'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.world.refresh_from_db()
        self.assertEqual(self.world.name, 'An Island')

    def test_spawn_world_and_missing_world_are_not_information_pages(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(reverse('lobby-world-information', args=[self.spawn_world.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('lobby-world-information', args=[99999999])).status_code, 404)

    def test_information_reads_are_bounded_and_lobby_does_not_load_content(self):
        self.client.force_authenticate(self.user)
        with CaptureQueriesContext(connection) as before:
            self.assertEqual(self.client.get(self.endpoint).status_code, 200)
        self.assertLessEqual(len(before), 4)
        for n in range(12):
            self.create_player(f'Player{n}')
        self.save('x' * 50000)
        with CaptureQueriesContext(connection) as after:
            self.assertEqual(self.client.get(self.endpoint).status_code, 200)
        self.assertEqual(len(after), len(before))
        with CaptureQueriesContext(connection) as lobby_queries:
            response = self.client.get(reverse('lobby-world-detail', args=[self.world.pk]))
        self.assertNotIn('information', response.data)
        self.assertFalse(any('worlds_worldinformation' in q['sql'] for q in lobby_queries))

    def test_yaml_partial_updates_validation_and_full_export_import(self):
        create_currency(world=self.world, code='obol', name='Obol')
        self.client.force_authenticate(self.user)
        apply_url = reverse('builder-world-manifest-apply', args=[self.world.pk])

        def apply(spec):
            return self.client.post(apply_url, {
                'manifest': manifest_stream_to_yaml([{'kind': 'world', 'spec': spec}]),
            }, format='json')

        self.assertEqual(apply({'information': self.markdown}).status_code, 200)
        self.assertEqual(apply({'name': 'Renamed'}).status_code, 200)
        self.assertEqual(world_information(self.world), self.markdown)
        for invalid in (None, 123, 'x' * 50001):
            self.assertEqual(apply({'information': invalid}).status_code, 400)
        exported = self.client.get(reverse('builder-world-export', args=[self.world.pk]))
        self.assertEqual(exported.status_code, 200, exported.data)
        target = World.objects.new_world(name='Destination', author=self.user)
        imported = self.client.post(reverse('builder-world-manifest-apply', args=[target.pk]), {
            'manifest': exported.data['yaml'],
        }, format='json')
        self.assertEqual(imported.status_code, 200, imported.data)
        self.assertEqual(world_information(target), self.markdown)
        self.assertEqual(apply({'information': ''}).status_code, 200)
        self.assertEqual(world_information(self.world), '')

    def test_instance_information_is_independent(self):
        self.client.force_authenticate(self.user)
        self.save(self.markdown)
        template = World.objects.new_world(name='Cave', author=self.user, instance_of=self.world)
        response = self.client.post(reverse('builder-world-manifest-apply', args=[template.pk]), {
            'manifest': manifest_stream_to_yaml([{'kind': 'world', 'spec': {'information': '## Cave'}}]),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(world_information(template), '## Cave')
        self.assertEqual(world_information(self.world), self.markdown)
