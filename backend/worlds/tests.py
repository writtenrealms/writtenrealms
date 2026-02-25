from mock import patch

from config import constants as adv_consts

from django.contrib.auth import get_user_model
from django.test import TestCase

from rest_framework.reverse import reverse
from rest_framework.test import APIRequestFactory, APITestCase

from config import constants as api_consts
from tests.base import WorldTestCase
from spawns.models import Player, Item, Equipment, Mob, PlayerEvent
from worlds.models import Room, World, Zone, WorldConfig, InstanceAssignment
from worlds.services import WorldSmith


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


class MultiplayerWorldTests(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save()
        self.spawn_world = self.world.create_spawn_world()

    def test_cleanup(self):
        player = Player.objects.create(
            world=self.spawn_world,
            room=self.room,
            name='John',
            user=self.user)
        sword = Item.objects.create(
            world=self.spawn_world,
            container=player.equipment,
            name='a sword')
        player.equipment.weapon = sword
        player.equipment.save()
        # apple in env
        apple = Item.objects.create(
            world=self.spawn_world,
            container=player,
            name='an apple')
        # rock on ground
        rock = Item.objects.create(
            world=self.spawn_world,
            container=self.room,
            name='a rock')
        # Scholar in room with a bag and a book on them
        scholar = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name='a scholar')
        bag = Item.objects.create(
            world=self.spawn_world,
            container=scholar,
            type=adv_consts.ITEM_TYPE_CONTAINER,
            name='a bag')
        book = Item.objects.create(
            world=self.spawn_world,
            container=bag,
            name='a book')

        # Check some preconditions
        self.assertEqual(self.room.inventory.count(), 1)
        self.assertEqual(scholar.inventory.count(), 1)
        self.assertEqual(self.room.mobs.count(), 1)

        # Cleanup!
        self.spawn_world.set_state(api_consts.WORLD_STATE_STOPPED)
        self.spawn_world.cleanup()

        # Rock is gone
        self.assertEqual(self.room.inventory.count(), 0)
        with self.assertRaises(Item.DoesNotExist):
            rock = Item.objects.get(pk=rock.pk)

        # Apple and sword still there
        apple = Item.objects.get(pk=apple.pk)
        sword = Item.objects.get(pk=sword.pk)

        # Scholar is gone, as is his book
        self.assertEqual(self.room.mobs.count(), 0)
        with self.assertRaises(Mob.DoesNotExist):
            scholar = Mob.objects.get(pk=scholar.pk)
        with self.assertRaises(Item.DoesNotExist):
            bag = Item.objects.get(pk=bag.pk)
        with self.assertRaises(Item.DoesNotExist):
            book = Item.objects.get(pk=book.pk)


class TestCreateWorld(APITestCase):

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            'joe@example.com', 'p')
        self.client.force_authenticate(self.user)

    def test_create_mpw_world(self):
        """
        # Can't create a MPW world without the is_builder flag
        endpoint = reverse('builder-world-list')
        resp = self.client.post(endpoint, {
            "name": "A New World",
            "is_multiplayer": True,
        })
        self.assertEqual(resp.status_code, 400)

        self.user.is_staff = True
        self.user.save()
        """

        endpoint = reverse('builder-world-list')
        resp = self.client.post(endpoint, {
            "name": "A New World",
            "is_multiplayer": True,
        })
        self.assertEqual(resp.status_code, 201)

        new_world = World.objects.get(pk=resp.data['id'])
        self.assertEqual(new_world.name, 'A New World')
        self.assertTrue(new_world.is_multiplayer)
        self.assertEqual(new_world.zones.count(), 1)
        self.assertEqual(new_world.rooms.count(), 1)

        #player_event = PlayerEvent.objects.get()
        #self.assertEqual(player_event.event, api_consts.PLAYER_EVENT_CREATE)

    def test_create_spw_world(self):
        self.assertFalse(self.user.is_builder)
        endpoint = reverse('builder-world-list')
        resp = self.client.post(endpoint, {
            "name": "A New World",
        })
        self.assertEqual(resp.status_code, 201)
        new_world = World.objects.get(pk=resp.data['id'])
        self.assertEqual(new_world.is_multiplayer, False)

        # When creating a SPW, a builder character is automatically created
        # for the author
        builder_char = new_world.spawned_worlds.get().players.get()
        self.assertTrue(builder_char.is_immortal)
        self.assertEqual(builder_char.user, self.user)

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



# Service tests


class StartWorldTests(WorldTestCase):

    def test_start_mpw(self):
        self.world.is_multiplayer = True
        self.world.save()
        spawn_world = self.world.create_spawn_world()
        spawn_world.start_mpw()
        self.assertEqual(spawn_world.lifecycle, api_consts.WORLD_STATE_RUNNING)
        self.assertEqual(spawn_world.game_world.state, adv_consts.WORLD_STATE_ONLINE)


class InstanceEntranceTests(WorldTestCase):

    def setUp(self):
        super().setUp()
        instance_config = WorldConfig.objects.create()
        self.instance_context = World.objects.new_world(
            name='An Instance',
            author=self.user,
            config=instance_config,
            is_multiplayer=True,
            instance_of=self.world)

    def test_single_player_enters_instance(self):
        "A single player, not part of a group, enters an instance"
        instance = self.instance_context.instance_for(
            player=self.player,
            transfer_from=self.room)
        self.assertEqual(InstanceAssignment.objects.count(), 1)
        instance_assignment = InstanceAssignment.objects.get()
        self.assertEqual(instance_assignment.instance, instance)
        self.assertEqual(instance_assignment.player, self.player)
        self.assertEqual(instance_assignment.instance.context,
                         self.instance_context)
        self.assertEqual(instance_assignment.instance.context.instance_of,
                         self.world)
        self.assertEqual(instance_assignment.transfer_from, self.room)
        self.assertEqual(instance.leader, self.player)

    def test_group_enters_instance(self):
        "A group of players enters an instance"
        player2 = Player.objects.create(
            name='Jane',
            world=self.spawn_world,
            room=self.room,
            user=self.user)

        # Group leader enters the instance
        instance = self.instance_context.instance_for(
            player=self.player,
            transfer_from=self.room,)
        self.assertEqual(InstanceAssignment.objects.count(), 1)

        self.assertIsNotNone(instance.instance_ref)
        self.assertEqual(instance.leader, self.player)

        # Group member enters the instance
        group_instance = self.instance_context.instance_for(
            player=player2,
            transfer_from=self.room,
            ref=instance.instance_ref)
        self.assertEqual(instance, group_instance)

        self.assertEqual(InstanceAssignment.objects.count(), 2)

    def test_player_enters_solo_then_joins_group(self):
        """
        Test for a player first entering an instance solo, then exiting it to
        join a group's instance.
        """
        player2 = Player.objects.create(
            name='Jane',
            world=self.spawn_world,
            room=self.room,
            user=self.user)

        # Create leader's instance
        leader_instance = self.instance_context.instance_for(
            player=self.player,
            transfer_from=self.room)
        self.assertEqual(leader_instance.leader, self.player)

        # Create the future member's instance
        member_solo_instance = self.instance_context.instance_for(
            player=player2,
            transfer_from=self.room)
        self.assertEqual(member_solo_instance.leader, player2)

        # Now have the member join the leader's instance
        group_instance = self.instance_context.instance_for(
            player=player2,
            transfer_from=self.room,
            ref=leader_instance.instance_ref)

        self.assertEqual(group_instance, leader_instance)
        self.assertEqual(group_instance.leader, self.player)
