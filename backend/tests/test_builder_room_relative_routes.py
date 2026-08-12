from django.urls import reverse

from builders.instance_templates import create_instance_template
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

    def test_room_list_searches_bare_numbers_by_relative_id(self):
        relative_id = self.room.pk + 1000
        target = Room.objects.create_with_imported_relative_id(
            world=self.world,
            zone=self.zone,
            relative_id=relative_id,
            name='Portable Search Target',
            x=relative_id,
            y=0,
            z=0,
        )
        self.assertNotEqual(target.pk, relative_id)

        response = self.client.get(
            reverse('builder-room-list', args=[self.world.pk]),
            {'query': str(relative_id)},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [room['manifest_ref'] for room in response.data['results']],
            [f'room@{relative_id}'],
        )

    def test_room_list_bare_number_does_not_select_colliding_database_id(self):
        filler_world = World.objects.new_world(
            name='Room search ID filler',
            author=self.user,
        )
        filler_zone = filler_world.zones.get()
        for index in range(3):
            Room.objects.create(
                world=filler_world,
                zone=filler_zone,
                name=f'Filler {index}',
                x=index + 10,
                y=0,
                z=0,
            )
        database_collision = Room.objects.create(
            world=self.world,
            zone=self.zone,
            name='Database Collision',
            x=20,
            y=0,
            z=0,
        )
        relative_id = database_collision.pk
        target = Room.objects.create_with_imported_relative_id(
            world=self.world,
            zone=self.zone,
            relative_id=relative_id,
            name='Relative Winner',
            x=21,
            y=0,
            z=0,
        )

        response = self.client.get(
            reverse('builder-room-list', args=[self.world.pk]),
            {'query': str(relative_id)},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [room['id'] for room in response.data['results']],
            [target.pk],
        )

    def test_room_list_accepts_typed_manifest_ref_search(self):
        relative_id = self.room.pk + 1001
        Room.objects.create_with_imported_relative_id(
            world=self.world,
            zone=self.zone,
            relative_id=relative_id,
            name='Typed Search Target',
            x=relative_id,
            y=0,
            z=0,
        )

        response = self.client.get(
            reverse('builder-room-list', args=[self.world.pk]),
            {'query': f'room@{relative_id}'},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [room['relative_id'] for room in response.data['results']],
            [relative_id],
        )

    def test_room_list_rejects_wrong_typed_reference(self):
        response = self.client.get(
            reverse('builder-room-list', args=[self.world.pk]),
            {'query': f'zone@{self.room.relative_id}'},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['results'], [])

    def test_room_list_rejects_out_of_range_relative_identity(self):
        for query in (str(1 << 63), f"room@{'9' * 5000}"):
            with self.subTest(query=query[:32]):
                response = self.client.get(
                    reverse('builder-room-list', args=[self.world.pk]),
                    {'query': query},
                )

                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['results'], [])

    def test_zone_room_list_uses_relative_identity_search(self):
        relative_id = self.room.pk + 1002
        Room.objects.create_with_imported_relative_id(
            world=self.world,
            zone=self.zone,
            relative_id=relative_id,
            name='Zone Search Target',
            x=relative_id,
            y=0,
            z=0,
        )

        response = self.client.get(
            reverse(
                'builder-zone-room-list',
                args=[self.world.pk, self.zone.pk],
            ),
            {'query': str(relative_id)},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [room['manifest_ref'] for room in response.data['results']],
            [f'room@{relative_id}'],
        )

    def test_instance_room_search_qualifies_template_local_refs(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        templates = []
        for name, slug in (
            ('The Ash Vault', 'ash-vault'),
            ('The Glass Vault', 'glass-vault'),
        ):
            templates.append(create_instance_template(
                base_world=self.world,
                author=self.user,
                name=name,
                instance_slug=slug,
            ))

        response = self.client.get(
            reverse('builder-instance-room-list', args=[self.world.pk]),
            {'query': 'room@1'},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            {
                (room['instance_scope'], room['manifest_ref'])
                for room in response.data['results']
            },
            {
                ('ash-vault', 'room@1'),
                ('glass-vault', 'room@1'),
            },
        )

        self.room.transfer_to = templates[0].config.starting_room
        self.room.save(update_fields=['transfer_to'])
        config_response = self.client.get(reverse(
            'builder-room-config',
            args=[self.world.pk, self.room.pk],
        ))
        self.assertEqual(config_response.status_code, 200, config_response.data)
        self.assertEqual(
            config_response.data['transfer_to']['instance_scope'],
            'ash-vault',
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
