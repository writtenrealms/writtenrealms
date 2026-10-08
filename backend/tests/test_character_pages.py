from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import OperationalError, close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.reverse import reverse
from rest_framework.test import APIClient

from config import constants as adv_consts
from lobby.characters import CharacterItems
from spawns.models import Item, Player
from tests.base import WorldTestCase
from tests.test_items import create_test_item
from users.models import User
from worlds.models import World


class TestCharacterPage(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.endpoint = reverse('lobby-character', args=[self.player.pk])

    def test_owner_sees_the_full_character(self):
        create_test_item(self.world, self.spawn_world, self.player, 'Apple')
        self.client.force_authenticate(self.user)
        data = self.client.get(self.endpoint).data
        self.assertEqual(data['view'], 'private')
        self.assertEqual(data['world']['id'], self.world.pk)
        self.assertTrue(data['can_manage_items'])
        self.assertEqual([item['name'] for item in data['character']['inventory']], ['Apple'])
        self.assertIn('stats', data['character'])
        self.assertIn('labels', data['world_config'])

    def test_others_see_public_headlines_only(self):
        self.player.description = 'A tall hoplite.'
        self.player.save(update_fields=['description'])
        self.world.is_public = True
        self.world.save(update_fields=['is_public'])
        self.client.force_authenticate(None)
        data = self.client.get(self.endpoint).data
        self.assertEqual(data['view'], 'public')
        self.assertEqual(data['name'], 'Joe')
        self.assertEqual(data['description'], 'A tall hoplite.')
        for private_field in ('character', 'last_connection_ts', 'in_game', 'world_config'):
            self.assertNotIn(private_field, data)

    def test_owner_can_transfer_after_completing_a_world(self):
        self.client.force_authenticate(self.user)
        self.assertFalse(self.client.get(self.endpoint).data['can_transfer'])
        self.spawn_world.lifecycle = adv_consts.WORLD_STATE_COMPLETE
        self.spawn_world.save(update_fields=['lifecycle'])
        data = self.client.get(self.endpoint).data
        self.assertTrue(data['can_transfer'])
        self.assertEqual(data['id'], self.player.pk)

    def test_public_equipment_uses_one_batch_query(self):
        self.world.is_public = True
        self.world.save(update_fields=['is_public'])
        self.client.force_authenticate(None)
        equipment = self.player.equipment
        for slot in adv_consts.EQUIPMENT_SLOTS:
            item = create_test_item(
                self.world, self.spawn_world, equipment, slot, set_instance_name=False,
            )
            setattr(equipment, slot, item)
            equipment.save(update_fields=[slot])
            with self.assertNumQueries(2):
                response = self.client.get(self.endpoint)
            self.assertEqual(response.status_code, 200)
            worn = {row['slot']: row['name'] for row in response.data['equipment']}
            self.assertEqual(worn[slot], slot)

    def test_author_without_a_character_can_view_private_world_characters(self):
        self.player.user = self.create_user('other@example.com')
        self.player.save(update_fields=['user'])
        self.client.force_authenticate(self.user)
        lobby = self.client.get(reverse('lobby-world-detail', args=[self.world.pk]))
        self.assertEqual(lobby.status_code, 200)
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['view'], 'public')
        self.assertNotIn('character', response.data)
        self.player.is_invisible = True
        self.player.save(update_fields=['is_invisible'])
        self.assertEqual(self.client.get(self.endpoint).status_code, 404)

    def test_private_worlds_and_deleted_characters_are_hidden(self):
        self.world.is_public = False
        self.world.save(update_fields=['is_public'])
        stranger = self.create_user('stranger@example.com')
        self.client.force_authenticate(stranger)
        self.assertEqual(self.client.get(self.endpoint).status_code, 404)
        # A player of the same private world can see other characters in it.
        self.create_player('Ally', user=stranger)
        self.assertEqual(self.client.get(self.endpoint).data['view'], 'public')

        self.player.pending_deletion_ts = self.player.created_ts
        self.player.save(update_fields=['pending_deletion_ts'])
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(self.endpoint).status_code, 404)

    def test_owner_edits_description_and_deletes(self):
        self.client.force_authenticate(self.create_user('other@example.com'))
        self.assertEqual(self.client.patch(self.endpoint, {'description': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(self.endpoint).status_code, 404)

        self.client.force_authenticate(self.user)
        response = self.client.patch(self.endpoint, {'description': '  Scarred by Marathon. '}, format='json')
        self.assertEqual(response.data['description'], 'Scarred by Marathon.')

        self.player.in_game = True
        self.player.save(update_fields=['in_game'])
        self.assertEqual(self.client.delete(self.endpoint).status_code, 400)
        self.player.in_game = False
        self.player.save(update_fields=['in_game'])
        self.assertEqual(self.client.delete(self.endpoint).status_code, 204)
        self.assertIsNotNone(Player.objects.get(pk=self.player.pk).pending_deletion_ts)


class TestCharacterItems(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.endpoint = reverse('lobby-character-items', args=[self.player.pk])
        self.client.force_authenticate(self.user)
        self.spear = create_test_item(
            self.world, self.spawn_world, self.player, 'Spear',
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE, equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
        )
        self.bag = create_test_item(
            self.world, self.spawn_world, self.player, 'Satchel', item_type=adv_consts.ITEM_TYPE_CONTAINER,
        )
        self.apple = create_test_item(self.world, self.spawn_world, self.player, 'Apple')

    def post(self, **data):
        return self.client.post(self.endpoint, data, format='json')

    def test_equip_and_remove(self):
        response = self.post(action='equip', item=self.spear.key)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['character']['equipment']['weapon']['name'], 'Spear')
        self.player.refresh_from_db()
        self.assertEqual(self.player.equipment.weapon_id, self.spear.pk)

        response = self.post(action='remove', item=self.spear.key)
        self.assertIsNone(response.data['character']['equipment']['weapon'])
        self.assertIn('Spear', [item['name'] for item in response.data['character']['inventory']])

    def test_put_into_and_take_out_of_a_bag(self):
        response = self.post(action='put', item=self.apple.key, container=self.bag.key)
        self.assertEqual(response.status_code, 200, response.data)
        self.apple.refresh_from_db()
        self.assertEqual(self.apple.container_id, self.bag.pk)

        response = self.post(action='take', item=self.apple.key, container=self.bag.key)
        self.assertEqual(response.status_code, 200, response.data)
        self.apple.refresh_from_db()
        self.assertEqual((self.apple.container_type.model, self.apple.container_id), ('player', self.player.pk))

    def test_refuses_while_in_game_and_for_items_not_owned(self):
        self.assertEqual(self.post(action='put', item=self.apple.key, container=self.apple.key).status_code, 400)
        other = self.create_player('Other', user=self.create_user('other@example.com'))
        theirs = create_test_item(self.world, self.spawn_world, other, 'Their Apple')
        self.assertEqual(self.post(action='equip', item=theirs.key).status_code, 400)
        self.assertEqual(self.post(action='equip', item='item.abc').status_code, 400)
        self.assertEqual(self.post(action='drop', item=self.apple.key).status_code, 400)

        self.player.in_game = True
        self.player.save(update_fields=['in_game'])
        response = self.post(action='equip', item=self.spear.key)
        self.assertEqual(response.status_code, 400)
        self.assertIn('in the game', str(response.data))
        self.assertEqual(Item.objects.get(pk=self.spear.pk).container_id, self.player.pk)

    def test_only_the_owner_can_move_items(self):
        self.client.force_authenticate(self.create_user('other@example.com'))
        self.assertEqual(self.post(action='equip', item=self.spear.key).status_code, 404)

    def test_item_queries_do_not_grow_with_room_occupants(self):
        actions = [
            {'action': 'equip', 'item': self.spear.key},
            {'action': 'remove', 'item': self.spear.key},
            {'action': 'put', 'item': self.apple.key, 'container': self.bag.key},
            {'action': 'take', 'item': self.apple.key, 'container': self.bag.key},
        ]

        def query_counts():
            counts = []
            # No game-facing actor, room, item, or observer payload is needed.
            with patch('spawns.actions.items.serialize_room') as room_payload:
                with patch('spawns.actions.items.serialize_actor') as actor_payload:
                    for data in actions:
                        with CaptureQueriesContext(connection) as queries:
                            response = self.post(**data)
                        self.assertEqual(response.status_code, 200, response.data)
                        counts.append(len(queries))
                    room_payload.assert_not_called()
                    actor_payload.assert_not_called()
            return counts

        query_counts()  # Warm Django's ContentType cache.
        baseline = query_counts()
        for index in range(30):
            other = self.create_player(f'Other{index}')
            other.in_game = True
            other.save(update_fields=['in_game'])
        self.assertEqual(query_counts(), baseline)


class TestCharacterItemConcurrency(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('item-owner@example.com', 'p')
        world = World.objects.new_world(name='Item concurrency', author=self.user)
        runtime = world.create_spawn_world()
        self.player = Player.objects.create(
            user=self.user, name='Owner', world=runtime, room=world.config.starting_room,
        )
        self.spear = create_test_item(
            world, runtime, self.player, 'Spear',
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_login_cannot_overtake_ownership_check_or_item_mutation(self):
        def try_login():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '100ms'")
                    Player.objects.filter(pk=self.player.pk).update(in_game=True)
                return True
            except OperationalError as error:
                # A competing login must wait for the lobby transaction.
                self.assertEqual(error.__cause__.pgcode, '55P03')
                return False
            finally:
                close_old_connections()

        from spawns.actions.items import EquipAction

        original_owned_item = CharacterItems._owned_item
        original_execute = EquipAction.execute
        attempts = []
        with ThreadPoolExecutor(max_workers=1) as executor:
            def owned_item(view, player, key):
                attempts.append(executor.submit(try_login).result(timeout=5))
                return original_owned_item(view, player, key)

            def execute(action, *args, **kwargs):
                attempts.append(executor.submit(try_login).result(timeout=5))
                return original_execute(action, *args, **kwargs)

            with patch.object(CharacterItems, '_owned_item', owned_item):
                with patch.object(EquipAction, 'execute', execute):
                    response = self.client.post(
                        reverse('lobby-character-items', args=[self.player.pk]),
                        {'action': 'equip', 'item': self.spear.key}, format='json',
                    )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(attempts, [False, False])
            # The response is serialized outside the lock, and login can proceed.
            self.assertTrue(executor.submit(try_login).result(timeout=5))
        self.player.refresh_from_db()
        self.assertEqual(self.player.equipment.weapon_id, self.spear.pk)
