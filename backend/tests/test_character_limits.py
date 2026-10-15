from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError
from rest_framework.reverse import reverse
from rest_framework.test import APIClient

from core.factions import normalize_player_creation_config
from core.player_creation import counted_characters, enforce_character_limit
from spawns.models import Player
from tests.base import WorldTestCase
from users.models import User
from worlds.models import World


class TestCharacterLimits(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.endpoint = reverse('lobby-world-chars', args=[self.world.pk])
        self.set_limit(1)

    def set_limit(self, maximum):
        self.world.config.player_creation = {'max_characters_per_account': maximum}
        self.world.config.save(update_fields=['player_creation'])

    def create_character(self, **extra):
        return self.client.post(self.endpoint, {'name': 'Newcomer', **extra}, format='json')

    def test_cap_rejects_before_allocating_a_spawn(self):
        worlds_before = World.objects.count()
        response = self.create_character()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['code'], 'character_limit_reached')
        self.assertEqual(World.objects.count(), worlds_before)
        self.assertEqual(counted_characters(self.world, self.user).count(), 1)

    def test_builder_avatar_does_not_count_and_input_cannot_spoof_one(self):
        self.player.is_builder = True
        self.player.save(update_fields=['is_builder'])
        response = self.create_character(is_builder=True)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(Player.objects.get(pk=response.data['id']).is_builder)
        self.assertEqual(self.create_character(name='Another').status_code, 400)

    def test_staff_ordinary_characters_still_count(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.create_character().status_code, 400)

    def test_deleted_character_frees_slot(self):
        response = self.client.delete(reverse('lobby-world-char', args=[self.world.pk, self.player.pk]))
        self.assertEqual(response.status_code, 204, response.data)
        self.assertEqual(self.create_character().status_code, 201)

    def test_other_accounts_and_worlds_do_not_consume_slots(self):
        other_user = self.create_user('other@example.com')
        self.player.user = other_user
        self.player.save(update_fields=['user'])
        other_world = World.objects.new_world(name='Other world', author=self.user)
        self.create_player('Elsewhere', world=other_world.create_spawn_world(), room=other_world.config.starting_room)
        self.assertEqual(self.create_character().status_code, 201)

    def test_character_in_instance_still_counts(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        instance = World.objects.new_world(name='Introduction', author=self.user, instance_of=self.world)
        self.player.world = instance.create_spawn_world()
        self.player.save(update_fields=['world'])
        self.assertEqual(self.create_character().status_code, 400)

    def test_raise_lower_and_remove_limit_preserve_existing_characters(self):
        self.set_limit(2)
        self.assertEqual(self.create_character().status_code, 201)
        self.assertEqual(self.create_character(name='Third').status_code, 400)
        self.set_limit(1)
        self.assertEqual(counted_characters(self.world, self.user).count(), 2)
        self.assertEqual(self.create_character(name='Third').status_code, 400)
        self.set_limit(None)
        self.assertEqual(self.create_character(name='Third').status_code, 201)

    def test_lobby_count_excludes_builder_and_deleted_characters(self):
        builder = self.create_player('Builder')
        builder.is_builder = True
        builder.save(update_fields=['is_builder'])
        from lobby.characters import delete_character
        delete_character(self.create_player('Retired'))
        response = self.client.get(reverse('lobby-world-detail', args=[self.world.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['character_creation_limit'], {'maximum': 1, 'used': 1})
        self.set_limit(None)
        response = self.client.get(reverse('lobby-world-detail', args=[self.world.pk]))
        self.assertIsNone(response.data['character_creation_limit'])

    def test_limit_check_uses_one_account_lock_and_one_bounded_query(self):
        self.set_limit(2)
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            enforce_character_limit(self.world, self.user)
        self.assertEqual(len(queries), 2)
        self.assertIn('FOR NO KEY UPDATE', queries[0]['sql'])
        self.assertIn('LIMIT 1 OFFSET 1', queries[1]['sql'])
        self.assertNotIn('COUNT(', queries[1]['sql'])
        self.set_limit(None)
        with self.assertNumQueries(0):
            enforce_character_limit(self.world, self.user)

    def test_policy_validation_and_partial_updates(self):
        existing = {'core_faction': {'mode': 'none'}, 'instance_routes': []}
        policy = normalize_player_creation_config({'max_characters_per_account': 1}, existing=existing)
        self.assertEqual(policy, {**existing, 'max_characters_per_account': 1})
        self.assertEqual(normalize_player_creation_config({'instance_routes': []}, existing=policy), policy)
        for invalid in (0, -1, True, False, 1.5, '1', [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                normalize_player_creation_config({'max_characters_per_account': invalid})
        self.assertIsNone(normalize_player_creation_config({'max_characters_per_account': None}, existing=policy)['max_characters_per_account'])


class TestConcurrentCharacterLimits(TransactionTestCase):
    def test_simultaneous_creation_only_uses_one_slot(self):
        user = User.objects.create_user('concurrent-slots@example.com', 'p')
        world = World.objects.new_world(name='Limited world', author=user, is_multiplayer=True)
        world.create_spawn_world()
        world.config.player_creation = {'max_characters_per_account': 1}
        world.config.save(update_fields=['player_creation'])
        endpoint = reverse('lobby-world-chars', args=[world.pk])
        gate = Barrier(2)

        def synchronized_check(world, user):
            gate.wait(timeout=10)
            enforce_character_limit(world, user)

        def create(name):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(User.objects.get(pk=user.pk))
                return client.post(endpoint, {'name': name}, format='json').status_code
            finally:
                close_old_connections()

        with patch('core.player_creation.enforce_character_limit', side_effect=synchronized_check):
            with ThreadPoolExecutor(max_workers=2) as executor:
                statuses = list(executor.map(create, ['First', 'Second']))
        self.assertEqual(sorted(statuses), [201, 400])
        self.assertEqual(counted_characters(world, user).count(), 1)
