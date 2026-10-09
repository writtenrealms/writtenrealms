from rest_framework.reverse import reverse

from builders.models import WorldBuilder
from config import constants as api_consts
from tests.base import WorldTestCase
from worlds.models import World


class TestWorldSearch(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.endpoint = reverse('lobby-worlds-search')
        self.other = self.create_user('other@example.com')

    def search_ids(self, query):
        response = self.client.get(self.endpoint, {'q': query})
        self.assertEqual(response.status_code, 200)
        return {world['id'] for world in response.data['results']}

    def test_public_worlds_are_searchable_by_name_or_description(self):
        named = World.objects.new_world(
            name='Coastal Realm', author=self.other, is_public=True,
        )
        described = World.objects.new_world(
            name='Harbor', description='A coastal adventure',
            author=self.other, is_public=True,
        )
        World.objects.new_world(
            name='Mountains', author=self.other, is_public=True,
        )
        World.objects.new_world(name='Coastal Secret', author=self.other)
        archived = World.objects.new_world(
            name='Coastal Ruins', author=self.other, is_public=True,
        )
        archived.lifecycle = api_consts.WORLD_STATE_ARCHIVED
        archived.save(update_fields=['lifecycle'])
        named.create_spawn_world()

        self.assertEqual(self.search_ids('COASTAL'), {named.pk, described.pk})

    def test_private_worlds_remain_searchable_to_authors_builders_and_players(self):
        owned = World.objects.new_world(name='Hidden Home', author=self.user)
        shared = World.objects.new_world(name='Hidden Workshop', author=self.other)
        WorldBuilder.objects.create(world=shared, user=self.user, builder_rank=1)
        played = World.objects.new_world(name='Hidden Adventure', author=self.other)
        instance = World.objects.new_world(
            name='Introduction', author=self.other, instance_of=played,
        )
        spawn = instance.create_spawn_world()
        self.create_player('Visitor', world=spawn, room=instance.config.starting_room)
        World.objects.new_world(name='Hidden Secret', author=self.other)

        self.assertEqual(self.search_ids('hidden'), {owned.pk, shared.pk, played.pk})

        spawn.lifecycle = api_consts.WORLD_STATE_ARCHIVED
        spawn.save(update_fields=['lifecycle'])
        self.assertEqual(self.search_ids('hidden'), {owned.pk, shared.pk})

    def test_empty_search_returns_no_worlds(self):
        self.assertEqual(self.search_ids(''), set())
