from django.urls import reverse

from tests.base import WorldTestCase
from worlds.models import Room, World


class TestBuilderRoomRelativeRoutes(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.endpoint = reverse(
            'builder-room-relative-detail',
            args=[self.world.pk, self.room.relative_id],
        )

    def test_relative_lookup_returns_room_identity(self):
        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], self.room.pk)
        self.assertEqual(response.data['relative_id'], self.room.relative_id)
        self.assertEqual(
            response.data['manifest_ref'],
            f'room@{self.room.relative_id}',
        )

    def test_relative_lookup_is_scoped_to_route_world(self):
        other_world = World.objects.new_world(
            name='Another World',
            author=self.user,
        )
        foreign_room = Room.objects.create_with_imported_relative_id(
            world=other_world,
            zone=other_world.zones.get(),
            relative_id=42,
            name='Foreign Room',
            x=42,
            y=0,
            z=0,
        )

        response = self.client.get(reverse(
            'builder-room-relative-detail',
            args=[self.world.pk, foreign_room.relative_id],
        ))

        self.assertEqual(response.status_code, 404)

    def test_room_list_exposes_portable_identity(self):
        response = self.client.get(reverse(
            'builder-room-list',
            args=[self.world.pk],
        ))

        self.assertEqual(response.status_code, 200, response.data)
        serialized_room = next(
            item
            for item in response.data['results']
            if item['id'] == self.room.pk
        )
        self.assertEqual(serialized_room['relative_id'], self.room.relative_id)
        self.assertEqual(
            serialized_room['manifest_ref'],
            f'room@{self.room.relative_id}',
        )

    def test_world_map_exposes_portable_room_identity(self):
        response = self.client.get(reverse(
            'builder-world-map',
            args=[self.world.pk],
        ))

        self.assertEqual(response.status_code, 200, response.data)
        serialized_room = response.data['rooms'][self.room.key]
        self.assertEqual(serialized_room['relative_id'], self.room.relative_id)
        self.assertEqual(
            serialized_room['manifest_ref'],
            f'room@{self.room.relative_id}',
        )

    def test_nested_room_reference_exposes_portable_identity(self):
        north_room = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name='North Room',
            x=0,
            y=1,
            z=0,
        )
        self.room.north = north_room
        self.room.save(update_fields=['north'])

        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['north']['id'], north_room.pk)
        self.assertEqual(
            response.data['north']['relative_id'],
            north_room.relative_id,
        )
        self.assertEqual(
            response.data['north']['manifest_ref'],
            f'room@{north_room.relative_id}',
        )

    def test_primary_key_detail_endpoint_remains_available(self):
        response = self.client.get(reverse(
            'builder-room-detail',
            args=[self.world.pk, self.room.pk],
        ))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], self.room.pk)
