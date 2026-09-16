from django.contrib.auth import get_user_model
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from builders.currencies import create_currency
from builders.instance_templates import create_instance_template
from builders.models import (
    BuilderAssignment, CraftingProfile, MerchantProfile, TrainerProfile, WorldBuilder,
)
from builders.world_export import (
    _serialize_room_manifest, manifest_stream_to_yaml, serialize_world_export_payload,
)
from spawns.merchants import create_or_update_room_merchant_runtime
from worlds.models import Room, World


class RoomServiceExportTests(APITestCase):
    services = ('merchant', 'crafting', 'trainer')

    def setUp(self):
        self.user = get_user_model().objects.create_user('room-service-export@example.com', 'p')
        self.client.force_authenticate(self.user)
        self.source = World.objects.new_world(
            name='Phalanx', author=self.user, is_multiplayer=True,
        )
        currency = create_currency(world=self.source, code='obol', name='Obol')
        self.profiles = {
            'merchant': MerchantProfile.objects.create(
                world=self.source, slug='market', name='Market', settlement_currency=currency,
            ),
            'crafting': CraftingProfile.objects.create(
                world=self.source, slug='forge', name='Forge',
            ),
            'trainer': TrainerProfile.objects.create(
                world=self.source, slug='training', name='Training',
            ),
        }
        self.room = self.source.config.starting_room
        self.room.name = 'Market Square'
        self.room.save(update_fields=['name'])

    def _set_services(self, room, *, attached):
        fields = []
        for service in self.services:
            field = f'{service}_profile'
            setattr(room, field, self.profiles[service] if attached else None)
            fields.append(field)
        room.save(update_fields=fields)

    def _apply(self, world, documents):
        return self.client.post(
            reverse('builder-world-manifest-apply', args=[world.pk]),
            {'manifest': manifest_stream_to_yaml(documents)}, format='json',
        )

    def _target(self):
        response = self.client.post(
            reverse('builder-world-list'),
            {'name': 'Destination', 'is_multiplayer': True}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return World.objects.get(pk=response.data['id'])

    def _round_trip(self, *, bundle):
        source_rooms = [self.room]
        if bundle:
            instance = create_instance_template(
                base_world=self.source, author=self.user, name='Outpost', instance_slug='outpost',
            )
            source_rooms.append(instance.config.starting_room)
        for room in source_rooms:
            self._set_services(room, attached=True)

        target = self._target()
        documents = serialize_world_export_payload(self.source)['documents']
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 200, response.data)
        imported_worlds = [target]
        if bundle:
            imported_worlds.append(World.objects.get(instance_of=target, instance_slug='outpost'))
        imported_rooms = [
            world.rooms.get(relative_id=source_room.relative_id)
            for world, source_room in zip(imported_worlds, source_rooms)
        ]
        runtimes = []
        for world, room in zip(imported_worlds, imported_rooms):
            self.assertNotEqual(world.pk, self.source.pk)
            for service in self.services:
                profile = getattr(room, f'{service}_profile')
                self.assertEqual(profile.slug, self.profiles[service].slug)
                self.assertEqual(profile.world_id, target.pk)
            runtime_world = world.spawned_worlds.filter(is_multiplayer=True).first()
            if runtime_world is None:
                runtime_world = world.create_spawn_world()
            runtimes.append(create_or_update_room_merchant_runtime(room, runtime_world))

        for room in source_rooms:
            self._set_services(room, attached=False)
        documents = serialize_world_export_payload(self.source)['documents']
        for _ in range(2):
            response = self._apply(target, documents)
            self.assertEqual(response.status_code, 200, response.data)
            for room in imported_rooms:
                room.refresh_from_db()
                for service in self.services:
                    self.assertIsNone(getattr(room, f'{service}_profile_id'))
            for runtime in runtimes:
                runtime.refresh_from_db()
                self.assertFalse(runtime.is_active)
        target.refresh_from_db()
        self.assertEqual(serialize_world_export_payload(target)['documents'], documents)
        for document in documents:
            if document['kind'] == 'room':
                for service in self.services:
                    self.assertIn(service, document['spec'])
                    self.assertIsNone(document['spec'][service])

    def test_world_export_reimport_clears_removed_room_services(self):
        self._round_trip(bundle=False)

    def test_family_export_reimport_clears_base_and_instance_room_services(self):
        self._round_trip(bundle=True)

    def test_partial_patches_preserve_omitted_services_and_clear_only_explicit_fields(self):
        for service in self.services:
            for empty in (None, {}):
                with self.subTest(service=service, empty=empty):
                    self._set_services(self.room, attached=True)
                    response = self._apply(self.source, [{
                        'kind': 'room', 'metadata': {'ref': f'room@{self.room.relative_id}'},
                        'spec': {service: empty, 'description': 'Updated description.'},
                    }])
                    self.assertEqual(response.status_code, 200, response.data)
                    self.room.refresh_from_db()
                    self.assertEqual(self.room.description, 'Updated description.')
                    for field in self.services:
                        self.assertEqual(
                            getattr(self.room, f'{field}_profile_id'),
                            None if field == service else self.profiles[field].pk,
                        )

    def test_later_import_failure_restores_cleared_attachments(self):
        self._set_services(self.room, attached=True)
        target = self._target()
        response = self._apply(target, serialize_world_export_payload(self.source)['documents'])
        self.assertEqual(response.status_code, 200, response.data)
        room = target.rooms.get(relative_id=self.room.relative_id)
        before = tuple(getattr(room, f'{service}_profile_id') for service in self.services)
        runtime_world = target.spawned_worlds.filter(is_multiplayer=True).get()
        runtime = create_or_update_room_merchant_runtime(room, runtime_world)

        self._set_services(self.room, attached=False)
        documents = serialize_world_export_payload(self.source)['documents']
        documents.append({
            'kind': 'world', 'spec': {'unsupported_setting': True},
        })
        response = self._apply(target, documents)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('unsupported_setting', str(response.data))
        room.refresh_from_db()
        self.assertEqual(
            tuple(getattr(room, f'{service}_profile_id') for service in self.services), before,
        )
        runtime.refresh_from_db()
        self.assertTrue(runtime.is_active)

    def test_export_uses_preloaded_profiles_without_additional_queries(self):
        for attached in (True, False):
            with self.subTest(attached=attached):
                self._set_services(self.room, attached=attached)
                room = Room.objects.select_related(
                    'zone', 'merchant_profile', 'crafting_profile', 'trainer_profile',
                ).get(pk=self.room.pk)
                room._export_flags = []
                room._export_details = []
                room._export_door_faces = []
                with self.assertNumQueries(0):
                    document = _serialize_room_manifest(room, include_empty_services=True)
                for service in self.services:
                    self.assertEqual(document['spec'][service], (
                        {'profile': f'{service}profile.{self.profiles[service].slug}'}
                        if attached else None
                    ))

    def test_individual_room_yaml_remains_editable_by_assigned_rank_two_builder(self):
        builder_user = get_user_model().objects.create_user('room-service-editor@example.com', 'p')
        builder = WorldBuilder.objects.create(world=self.source, user=builder_user, builder_rank=2)
        BuilderAssignment.objects.create(builder=builder, assignment=self.room)
        self.client.force_authenticate(builder_user)
        response = self.client.get(reverse(
            'builder-room-manifest', args=[self.source.pk, self.room.pk],
        ))
        self.assertEqual(response.status_code, 200, response.data)
        document = response.data['manifest']
        self.assertNotIn('trainer', document['spec'])
        document['spec']['description'] = 'Edited from the room YAML.'
        response = self._apply(self.source, [document])
        self.assertEqual(response.status_code, 200, response.data)
        self.room.refresh_from_db()
        self.assertEqual(self.room.description, 'Edited from the room YAML.')
