from django.urls import reverse

from tests.base import WorldTestCase
from worlds.models import World, Zone


class TestBuilderZoneRelativeRoutes(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.endpoint = reverse(
            'builder-zone-relative-detail',
            args=[self.world.pk, self.zone.relative_id],
        )

    def test_relative_lookup_returns_zone_identity(self):
        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], self.zone.pk)
        self.assertEqual(response.data['relative_id'], self.zone.relative_id)
        self.assertEqual(
            response.data['manifest_ref'],
            f'zone@{self.zone.relative_id}',
        )

    def test_zone_list_accepts_relative_identity_search(self):
        response = self.client.get(
            reverse('builder-zone-list', args=[self.world.pk]),
            {'query': f'zone@{self.zone.relative_id}'},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [zone['manifest_ref'] for zone in response.data['results']],
            [f'zone@{self.zone.relative_id}'],
        )

    def test_zone_list_bare_number_ignores_colliding_database_id(self):
        filler_world = World.objects.new_world(
            name='Zone search ID filler',
            author=self.user,
        )
        for index in range(3):
            Zone.objects.create(
                world=filler_world,
                name=f'Filler Zone {index}',
            )
        database_collision = Zone.objects.create(
            world=self.world,
            name='Database Collision',
        )
        relative_id = database_collision.pk
        target = Zone.objects.create(
            world=self.world,
            relative_id=relative_id,
            name='Relative Winner',
        )

        response = self.client.get(
            reverse('builder-zone-list', args=[self.world.pk]),
            {'query': str(relative_id)},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [zone['id'] for zone in response.data['results']],
            [target.pk],
        )

    def test_zone_list_rejects_room_reference(self):
        response = self.client.get(
            reverse('builder-zone-list', args=[self.world.pk]),
            {'query': f'room@{self.zone.relative_id}'},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['results'], [])

    def test_relative_lookup_is_scoped_to_route_world(self):
        other_world = World.objects.new_world(
            name='Another World',
            author=self.user,
        )
        foreign_zone = Zone.objects.create(
            world=other_world,
            name='Foreign Zone',
        )

        response = self.client.get(reverse(
            'builder-zone-relative-detail',
            args=[self.world.pk, foreign_zone.relative_id],
        ))

        self.assertEqual(response.status_code, 404)

    def test_relative_lookup_is_get_only(self):
        response = self.client.put(
            self.endpoint,
            {'name': 'Renamed Zone'},
            format='json',
        )

        self.assertEqual(response.status_code, 405)
        self.zone.refresh_from_db()
        self.assertNotEqual(self.zone.name, 'Renamed Zone')

    def test_nested_zone_reference_exposes_portable_identity(self):
        response = self.client.get(reverse(
            'builder-room-detail',
            args=[self.world.pk, self.room.pk],
        ))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['zone']['id'], self.zone.pk)
        self.assertEqual(
            response.data['zone']['relative_id'],
            self.zone.relative_id,
        )
        self.assertEqual(
            response.data['zone']['manifest_ref'],
            f'zone@{self.zone.relative_id}',
        )

    def test_primary_key_detail_endpoint_remains_available(self):
        response = self.client.get(reverse(
            'builder-zone-detail',
            args=[self.world.pk, self.zone.pk],
        ))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], self.zone.pk)
