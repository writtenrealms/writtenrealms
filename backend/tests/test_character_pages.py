from rest_framework.reverse import reverse

from config import constants as adv_consts
from spawns.models import Item, Player
from tests.base import WorldTestCase
from tests.test_items import create_test_item


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
