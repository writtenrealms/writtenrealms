from rest_framework.reverse import reverse

from builders.models import LastViewedRoom, WorldBuilder
from config import constants as api_consts
from system.models import SiteControl
from tests.base import WorldTestCase
from worlds.models import World


class TestBuildingSiteControl(WorldTestCase):

    def create_world(self, user):
        self.client.force_authenticate(user)
        return self.client.post(reverse('builder-world-list'), {
            'name': 'New Realm', 'is_multiplayer': True,
        })

    def test_building_is_open_without_a_site_control(self):
        SiteControl.objects.all().delete()
        self.assertEqual(self.create_world(self.user).status_code, 201)
        self.assertTrue(self.client.get(reverse('lobby-config')).data['building_enabled'])

    def test_disabled_building_blocks_regular_users_but_not_staff(self):
        SiteControl.objects.update_or_create(name='prod', defaults={'building_enabled': False})
        response = self.create_world(self.user)
        self.assertEqual(response.status_code, 400)
        self.assertIn('World creation is currently disabled.', str(response.data))
        staff = self.create_user('staff@example.com', is_staff=True)
        self.assertEqual(self.create_world(staff).status_code, 201)
        self.assertFalse(self.client.get(reverse('lobby-config')).data['building_enabled'])

    def test_disabled_building_keeps_access_to_existing_worlds(self):
        SiteControl.objects.update_or_create(name='prod', defaults={'building_enabled': False})
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse('lobby-worlds-building'))
        self.assertEqual([row['id'] for row in response.data], [self.world.pk])

    def test_only_staff_can_change_site_settings(self):
        endpoint = reverse('staff-site-settings')
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.patch(endpoint, {'building_enabled': False}, format='json').status_code, 403)
        self.assertEqual(self.client.get(reverse('staff_panel')).status_code, 403)

        staff = self.create_user('staff@example.com', is_staff=True)
        self.client.force_authenticate(staff)
        response = self.client.patch(endpoint, {'building_enabled': False}, format='json')
        self.assertEqual(response.data, {'building_enabled': False})
        self.assertFalse(SiteControl.objects.get(name='prod').building_enabled)
        self.assertEqual(self.client.patch(endpoint, {'building_enabled': 'no'}, format='json').status_code, 400)
        self.assertFalse(self.client.get(reverse('staff_panel')).data['building_enabled'])


class TestBuildingInventory(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.endpoint = reverse('lobby-worlds-building')
        self.client.force_authenticate(self.user)

    def rows(self):
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_lists_authored_and_shared_worlds_with_nested_instances(self):
        other = self.create_user('other@example.com')
        shared = World.objects.new_world(name='Shared Realm', author=other)
        WorldBuilder.objects.create(world=shared, user=self.user, builder_rank=1)
        instance = World.objects.new_world(name='Outpost', author=self.user, instance_of=self.world)
        foreign_instance = World.objects.new_world(name='Their Arena', author=self.user, instance_of=shared)
        World.objects.new_world(name='Not Mine', author=other)
        archived = World.objects.new_world(name='Old Realm', author=self.user)
        archived.lifecycle = api_consts.WORLD_STATE_ARCHIVED
        archived.save(update_fields=['lifecycle'])
        LastViewedRoom.objects.create(world=shared, user=self.user, room=shared.zones.all()[0].rooms.all()[0])

        rows = self.rows()
        by_name = {row['name']: row for row in rows}
        # Recently opened first; instances sit under their base world.
        self.assertEqual(rows[0]['name'], 'Shared Realm')
        self.assertEqual(set(by_name), {'Shared Realm', 'An Island'})
        self.assertEqual(by_name['An Island']['role'], 'author')
        self.assertEqual(by_name['An Island']['num_characters'], 1)
        self.assertGreaterEqual(by_name['An Island']['num_rooms'], 1)
        self.assertEqual([i['id'] for i in by_name['An Island']['instances']], [instance.pk])
        self.assertEqual(by_name['Shared Realm']['role'], 'builder')
        self.assertEqual([i['id'] for i in by_name['Shared Realm']['instances']], [foreign_instance.pk])
        self.assertIsNone(by_name['Shared Realm']['instances'][0]['instance_of'])

    def test_instance_without_its_base_world_names_the_base(self):
        other = self.create_user('other@example.com')
        base = World.objects.new_world(name='Their Realm', author=other)
        instance = World.objects.new_world(name='Arena', author=other, instance_of=base)
        WorldBuilder.objects.create(world=instance, user=self.user, builder_rank=1)
        row = next(row for row in self.rows() if row['id'] == instance.pk)
        self.assertEqual(row['instance_of'], {'id': base.pk, 'name': 'Their Realm'})

    def test_query_count_does_not_grow_with_worlds(self):
        with self.assertNumQueries(1):
            self.client.get(self.endpoint)
        for index in range(4):
            World.objects.new_world(name=f'Realm {index}', author=self.user)
        with self.assertNumQueries(1):
            self.assertEqual(len(self.client.get(self.endpoint).data), 5)
