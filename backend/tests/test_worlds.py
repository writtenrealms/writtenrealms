from django.contrib.auth import get_user_model
from django.test import TestCase

from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from tests.base import WorldTestCase
from spawns.models import Player
from worlds.models import Room, World, Zone, WorldConfig
from worlds.serializers import ZoneSerializer


class WorldBasicTestCase(WorldTestCase):

    def test_keyed_endpoint_access(self):
        self.client.force_authenticate(self.user)
        world = World.objects.create(
            name='A World',
            author=self.user)
        resp = self.client.get(
            reverse('lobby-world-detail', args=[world.key]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['key'], world.key)

    def test_rename_world(self):
        "Tests that renaming a world also renames all of its spawned worlds"
        self.world.is_multiplayer = False
        self.world.save()

        self.client.force_authenticate(self.user)
        spawn1 = self.world.create_spawn_world()
        spawn2 = self.world.create_spawn_world()
        self.assertEqual(self.world.name, 'An Island')
        self.assertFalse(self.world.is_public)
        self.assertEqual(spawn1.name, 'An Island')
        self.assertEqual(spawn2.name, 'An Island')
        self.assertFalse(spawn1.is_public)
        self.assertFalse(spawn2.is_public)

        ep = reverse('builder-world-detail', args=[self.world.pk])
        resp = self.client.put(ep, {
            'name': 'An Isle',
            'is_public': True
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.world.refresh_from_db()
        spawn1.refresh_from_db()
        spawn2.refresh_from_db()
        self.assertEqual(self.world.name, 'An Isle')
        self.assertTrue(self.world.is_public)
        self.assertEqual(spawn1.name, 'An Isle')
        self.assertEqual(spawn2.name, 'An Isle')
        self.assertTrue(spawn1.is_public)
        self.assertTrue(spawn2.is_public)


class ZoneTestCase(APITestCase):

    def test_zone_numbering(self):
        world1 = World.objects.create(name='A Test World')
        world1zone1 = Zone.objects.create(name='W1 Zone One', world=world1)
        world1zone2 = Zone.objects.create(name='W1 Zone Two', world=world1)

        world2 = World.objects.create(name='Another Test World')
        world2zone1 = Zone.objects.create(name='W2 Zone One', world=world2)
        world2zone2 = Zone.objects.create(name='W2 Zone Two', world=world2)

        self.assertEqual(world1zone1.relative_id, 1)
        self.assertEqual(world1zone2.relative_id, 2)
        self.assertEqual(world2zone1.relative_id, 1)
        self.assertEqual(world2zone2.relative_id, 2)

    def test_zone_serializer_preserves_concurrent_unsubmitted_fields(self):
        world = World.objects.create(name='Serializer World')
        zone = Zone.objects.create(
            name='Original Zone',
            description='Original description',
            world=world,
        )
        stale_zone = Zone.objects.get(pk=zone.pk)
        Zone.objects.filter(pk=zone.pk).update(name='Concurrent name')
        serializer = ZoneSerializer(
            stale_zone,
            data={'description': 'Updated description'},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        serializer.save()

        zone.refresh_from_db()
        self.assertEqual(zone.name, 'Concurrent name')
        self.assertEqual(zone.description, 'Updated description')


class RoomTestCase(APITestCase):

    def test_room_numbering(self):
        world1 = World.objects.create(name='A Test World')
        world1room1 = Room.objects.create(
            name='W1 Room One', world=world1, x=0, y=0, z=0)
        world1room2 = Room.objects.create(
            name='W1 Room Two', world=world1, x=1, y=0, z=0)

        world2 = World.objects.create(name='Another Test World')
        world2room1 = Room.objects.create(
            name='W2 Room One', world=world2, x=0, y=0, z=0)
        world2room2 = Room.objects.create(
            name='W2 Room Two', world=world2, x=1, y=0, z=0)

        self.assertEqual(world1room1.relative_id, 1)
        self.assertEqual(world1room2.relative_id, 2)
        self.assertEqual(world2room1.relative_id, 1)
        self.assertEqual(world2room2.relative_id, 2)


class NewWorldCreation(TestCase):

    def test_new_world(self):
        world = World.objects.new_world(name='A world')
        self.assertEqual(world.zones.all()[0].name, 'Starting Zone')
        room = world.zones.all()[0].rooms.all()[0]
        self.assertEqual(room.name, 'Starting Room')
        self.assertEqual(world.config.starting_room, room)
        self.assertEqual(world.config.death_room, room)
        self.assertEqual(world.config.configured_worlds.get(), world)

    def test_new_world_uses_provided_config(self):
        config = WorldConfig.objects.create()
        world = World.objects.new_world(name='A world', config=config)
        room = world.zones.all()[0].rooms.all()[0]
        world.refresh_from_db()
        config.refresh_from_db()

        self.assertEqual(world.config_id, config.id)
        self.assertEqual(config.starting_room, room)
        self.assertEqual(config.death_room, room)


class TestCreateWorld(APITestCase):

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            'joe@example.com', 'p')
        self.client.force_authenticate(self.user)

    def test_cannot_create_world_as_guest_user(self):
        self.user.is_temporary = True
        self.user.save()

        endpoint = reverse('builder-world-list')
        resp = self.client.post(endpoint, {
            "name": "A New World",
        })
        self.assertEqual(resp.status_code, 400)


class WorldDeletionTests(WorldTestCase):

    def test_delete_spawn_world(self):
        spawn_world = self.world.create_spawn_world()
        self.assertEqual(spawn_world.config, self.world.config)

        player = Player.objects.create(
            name='John',
            world=spawn_world,
            room=self.room,
            user=self.user)

        spawn_world.delete()

        world = World.objects.get(pk=self.world.pk)
        self.assertIsNotNone(world.config)

        with self.assertRaises(Player.DoesNotExist):
            Player.objects.get(pk=player.id)
