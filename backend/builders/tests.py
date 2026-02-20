import mock
import collections

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers
from rest_framework.reverse import reverse

from config import constants as adv_consts

from config import game_settings as adv_config
from core.utils.mobs import suggest_stats

from config import constants as api_consts
from builders.models import (
    BuilderAssignment,
    Currency,
    ItemTemplate,
    ItemTemplateInventory,
    ItemAction,
    MobTemplate,
    MobTemplateInventory,
    TransformationTemplate,
    Quest,
    Objective,
    Loader,
    Rule,
    Path,
    PathRoom,
    Procession,
    Faction,
    FactionAssignment,
    FactionRank,
    FactSchedule,
    Reward,
    RoomCheck,
    RoomAction,
    Trigger,
    RandomItemProfile,
    MerchantInventory,
    WorldBuilder,
    WorldReview)
from builders import serializers as builder_serializers
from tests.base import WorldTestCase
from spawns import serializers as spawn_serializers
from spawns.models import Player, Mob, DoorState
from users.models import User
from worlds.models import World, Zone, Room, RoomFlag, RoomDetail, Door


# Base class
class BuilderTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)


class TestCreateWorld(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.endpoint = reverse('builder-world-list')

    def test_successful_creation_spw(self):
        resp = self.client.post(self.endpoint, {'name': 'A New World'})
        self.assertEqual(resp.status_code, 201)

        world = World.objects.get(pk=resp.data['id'])
        self.assertEqual(world.author, self.user)
        self.assertFalse(world.is_multiplayer)

        # a player got created
        player = world.spawned_worlds.get().players.get()
        # and a config
        self.assertIsNotNone(player.config)

    def test_successful_creation_mpw(self):
        self.user.is_staff = True
        self.user.save()

        resp = self.client.post(self.endpoint, {
            'name': 'A New World',
            'is_multiplayer': True,
        })
        self.assertEqual(resp.status_code, 201)

        world = World.objects.get(pk=resp.data['id'])
        self.assertEqual(world.author, self.user)
        self.assertTrue(world.is_multiplayer)

        spawned_world = world.spawned_worlds.get()
        self.assertTrue(spawned_world.is_multiplayer)

    def test_multiplayer_worlds_user_can_create_mpw(self):
        self.user.multiplayer_worlds = True
        self.user.save()

        resp = self.client.post(self.endpoint, {
            'name': 'A New World',
            'is_multiplayer': True,
        })
        self.assertEqual(resp.status_code, 201)

    def test_multiplayer_worlds_user_can_create_multiple_mpw(self):
        self.user.multiplayer_worlds = True
        self.user.save()

        self.world.is_multiplayer = True
        self.world.save()

        self.assertEqual(self.world.author, self.user)
        self.assertTrue(self.world.is_multiplayer)

        resp = self.client.post(self.endpoint, {
            'name': 'A New World',
            'is_multiplayer': True,
        })
        self.assertEqual(resp.status_code, 201)

    def test_create_instance(self):
        self.world.is_multiplayer = True
        self.world.save()
        resp = self.client.post(self.endpoint, {
            'name': 'New World Instance',
            'instance_of': self.world.pk,
        })
        self.assertEqual(resp.status_code, 201)
        instance = World.objects.get(pk=resp.json()['id'])
        self.assertEqual(instance.name, 'New World Instance')
        self.assertEqual(instance.instance_of, self.world)

    def test_cannot_create_instance_of_spw(self):
        self.world.is_multiplayer = False
        self.world.save()
        resp = self.client.post(self.endpoint, {
            'name': 'New World Instance',
            'instance_of': self.world.pk,
        })
        self.assertEqual(resp.status_code, 400)


class TestDeleteWorld(WorldTestCase):

    def test_delete_world_archives_it(self):
        self.client.force_authenticate(self.user)

        self.world.lifecycle = 'stored'
        self.world.save(update_fields=['lifecycle'])

        resp = self.client.delete(
            reverse('builder-world-detail',
            args=[self.world.id]))
        self.assertEqual(resp.status_code, 204)

        self.world.refresh_from_db()
        self.assertEqual(self.world.lifecycle, 'archived')

        # archived world doesn't show in list screen or detail
        # screen
        resp = self.client.get(reverse('builder-world-list'))
        self.assertEqual(len(resp.data['results']), 0)


class TestEditWorld(WorldTestCase):

    def test_toggle_maintenance_mode(self):
        self.client.force_authenticate(self.user)
        self.assertFalse(self.world.maintenance_mode)
        msg = 'World is down for maintenance.'
        resp = self.client.patch(
            reverse('builder-world-detail', args=[self.world.id]),
            {
                'maintenance_mode': True,
                'maintenance_msg': msg,
            })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['maintenance_mode'])
        self.assertEqual(resp.data['maintenance_msg'], msg)
        self.world.refresh_from_db()
        self.assertTrue(self.world.maintenance_mode)
        self.assertEqual(self.world.maintenance_msg, msg)


class TestZoneEndpoints(BuilderTestCase):
    "Also serves as 'basic endpoints' test."

    def setUp(self):
        super().setUp()
        self.list_ep = reverse('builder-zone-list', args=[self.world.pk])
        self.detail_ep = reverse(
            'builder-zone-detail', args=[self.world.pk, self.zone.pk])

    def test_get_zones(self):
        resp = self.client.get(self.list_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'][0]['key'], self.zone.key)

    def test_create_zone(self):
        resp = self.client.post(self.list_ep, {'name': 'A new zone'})
        self.assertEqual(resp.status_code, 201)

    def test_get_zone(self):
        resp = self.client.get(self.detail_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['key'], self.zone.key)

    def test_edit_zone(self):
        resp = self.client.put(self.detail_ep, {'name': 'Renamed zone'})
        self.assertEqual(resp.status_code, 200)
        zone = Zone.objects.get(pk=resp.data['id']) # refresh
        self.assertEqual(zone.name, 'Renamed zone')

    def test_delete_zone(self):
        zone = Zone.objects.create(world=self.world)
        resp = self.client.delete(
            reverse('builder-zone-detail', args=[self.world.pk, zone.pk]))
        self.assertEqual(resp.status_code, 204)
        with self.assertRaises(Zone.DoesNotExist):
            Zone.objects.get(pk=zone.pk)


class TestMoveZone(BuilderTestCase):

    def test_move_zone(self):
        self.assertEqual(self.room.x, 0)
        self.assertEqual(self.room.y, 0)
        self.assertEqual(self.room.z, 0)
        ep = reverse('builder-zone-move', args=[self.world.pk, self.zone.pk])
        resp = self.client.post(ep, {
            'direction': 'east',
            'distance': 2,
        })
        self.assertEqual(resp.status_code, 201)
        self.room.refresh_from_db()
        self.assertEqual(self.room.x, 2)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['x'], 2)


class TestRoomEndpoints(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.detail_endpoint = reverse('builder-room-detail',
                                       args=[self.world.pk, self.room.pk])

    def test_create_room(self):
        ep = reverse('builder-room-list', args=[self.world.pk])
        resp = self.client.post(ep, {
            'zone': {'key': self.zone.key},
            'name': 'New Room',
            'x': 1,
            'y': 0,
            'z': 0,
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        room = Room.objects.get(pk=resp.data['id'])
        self.assertEqual(room.name, 'New Room')

    def test_edit_room(self):
        zone = Zone.objects.create(world=self.world, name='Awesome zone')
        data = {
            'name': 'A better room',
            'x': 3,
            'zone': zone.key,
        }
        resp = self.client.put(self.detail_endpoint, data)
        self.assertEqual(resp.status_code, 200)
        room = Room.objects.get(pk=self.room.pk)
        self.assertEqual(room.name, 'A better room')
        self.assertEqual(room.x, 3)
        self.assertEqual(room.y, self.room.y)
        self.assertEqual(room.zone, zone)

    def test_edit_room_rejects_exit_to_other_world(self):
        east_room = Room.objects.create(world=self.world, x=1, y=0, z=0)
        self.room.east = east_room
        self.room.save()

        other_world = World.objects.new_world(
            name='Another World',
            author=self.user)
        other_room = other_world.rooms.first()

        resp = self.client.put(self.detail_endpoint, {
            'east': other_room.key,
        })
        self.assertEqual(resp.status_code, 400)
        self.room.refresh_from_db()
        self.assertEqual(self.room.east, east_room)

    def test_coordinate_conflict(self):
        # Tests that if a payload that doesn't change the coordinates is
        # passed, nothing bad happens
        east_room = self.room.create_at('east')

        resp = self.client.put(self.detail_endpoint, {
            'x': east_room.x,
            'y': east_room.y,
            'z': east_room.z,
        })
        self.assertEqual(resp.status_code, 400)

    def test_delete_room(self):
        # Create a connected room to make sure deleting the room doesn't
        # cascade delete other rooms
        north_room = Room.objects.create(world=self.world, x=0, y=1, z=1)
        south_room = Room.objects.create(world=self.world, x=0, y=0, z=1)

        north_room.south = south_room
        north_room.save()
        south_room.north = north_room
        south_room.save()

        resp = self.client.delete(
            reverse('builder-room-detail',
                    args=[self.world.pk, south_room.pk]))
        self.assertEqual(resp.status_code, 204)

        # self.room is gone
        with self.assertRaises(Room.DoesNotExist):
            Room.objects.get(pk=south_room.pk)

        # north_room is still there, but has its south exit nulled out
        north_room = Room.objects.get(pk=north_room.pk)

    def test_delete_starting_room_sets_another(self):
        config = self.world.config
        self.assertEqual(config.starting_room, self.room)
        north_room = Room.objects.create(world=self.world, x=0, y=1, z=1)
        resp = self.client.delete(
            reverse('builder-room-detail',
                    args=[self.world.pk, self.room.pk]))
        self.assertEqual(resp.status_code, 204)
        config.refresh_from_db()
        self.assertEqual(config.starting_room, north_room)

    def test_cannot_delete_room_with_online_player_in_it(self):
        spawned_world = self.world.create_spawn_world()
        # Otherwise we'd get a 'cannot delete last room in world' error
        north_room = Room.objects.create(world=self.world, x=0, y=1, z=1)
        player = Player.objects.create(
            name='John',
            room=self.room,
            user=self.user,
            world=spawned_world,
            in_game=True)
        resp = self.client.delete(
            reverse('builder-room-detail',
                    args=[self.world.pk, self.room.pk]))
        self.assertEqual(resp.status_code, 400)

    def test_deleting_room_with_player_in_it(self):
        spawned_world = self.world.create_spawn_world()
        north_room = Room.objects.create(
            name='New Room',
            world=self.world, x=0, y=1, z=1)
        player = Player.objects.create(
            name='John',
            room=self.room,
            user=self.user,
            world=spawned_world,
            in_game=False)
        resp = self.client.delete(
            reverse('builder-room-detail',
                    args=[self.world.pk, self.room.pk]))
        self.assertEqual(resp.status_code, 204)
        player.refresh_from_db()
        self.assertEqual(player.room, north_room)

    def test_room_flags(self):
        # Make sure flags are false by default
        resp = self.client.get(self.detail_endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['is_no_roam'])

        # Adding the Flag object changes the value to True
        RoomFlag.objects.create(room=self.room, code='no_roam')
        resp = self.client.get(self.detail_endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_no_roam'])

        # Change the value back to False
        resp = self.client.put(self.detail_endpoint, {
            'is_no_roam': False,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            RoomFlag.objects.filter(
                room=self.room,
                code='no_roam').exists())

        # Change the value back to False
        resp = self.client.put(self.detail_endpoint, {
            'is_no_roam': True,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            RoomFlag.objects.filter(
                room=self.room,
                code='no_roam').exists())

    def test_cannot_delete_last_room(self):
        resp = self.client.delete(
            reverse('builder-room-detail',
                    args=[self.world.pk, self.room.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                         'Cannot delete the last room in a world.')

    def test_deleting_starting_room_sets_another(self):
        north_room = Room.objects.create(world=self.world, x=0, y=1, z=1)
        world = World.objects.get(pk=north_room.world.id)
        resp = self.client.delete(
            reverse('builder-room-detail',
                    args=[self.world.pk, self.room.pk]))
        self.assertEqual(resp.status_code, 204)

        config = north_room.world.config
        config.refresh_from_db()
        self.assertEqual(north_room.world.config.starting_room, north_room)
        self.assertEqual(north_room.world.config.death_room, north_room)


class RoomDirActionTests(WorldTestCase):

    # Mutual

    def test_set_mutual_from_neighbor(self):
        room2 = Room.objects.create(
            world=self.world,
            x=1, y=0, z=0) # has to be here

        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_MUTUAL,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save()

        # Reload & test
        self.room = Room.objects.get(pk=self.room.pk)
        room2 = Room.objects.get(pk=room2.pk)
        self.assertEqual(self.room.east, room2)
        self.assertEqual(room2.west, self.room)

    def test_set_mutual_from_inbound(self):
        room2 = Room.objects.create(
            world=self.world,
            x=2, y=1, z=0,
            west=self.room) # coords don't matter but has to be west

        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_MUTUAL,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save(room=self.room)

        # Reload & test
        self.room = Room.objects.get(pk=self.room.pk)
        room2 = Room.objects.get(pk=room2.pk)
        self.assertEqual(self.room.east, room2)
        self.assertEqual(room2.west, self.room)

    def test_set_mutual_from_outbound(self):
        room2 = Room.objects.create(world=self.world, x=2, y=1, z=0)
        self.room.east = room2 # coords don't matter but has to be east
        self.room.save()

        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_MUTUAL,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save()

        # Reload & test
        self.room = Room.objects.get(pk=self.room.pk)
        room2 = Room.objects.get(pk=room2.pk)
        self.assertEqual(self.room.east, room2)
        self.assertEqual(room2.west, self.room)

    # Disconnect

    def test_disconnect(self):
        room2 = Room.objects.create(
            world=self.world,
            x=1, y=0, z=0,
            west=self.room)
        self.room.east = room2
        self.room.save()

        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_NO_EXIT,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save()

        # Reload & test
        self.room = Room.objects.get(pk=self.room.pk)
        room2 = Room.objects.get(pk=room2.pk)
        self.assertIsNone(self.room.east)
        self.assertIsNone(room2.west)

    # Set one way

    def test_set_one_way(self):
        room2 = Room.objects.create(
            world=self.world,
            x=3, y=3, z=0,
            west=self.room)
        self.room.east = room2
        self.room.save()

        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_ONE_WAY,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save()

        # Reload & test
        self.room = Room.objects.get(pk=self.room.pk)
        room2 = Room.objects.get(pk=room2.pk)
        self.assertEqual(self.room.east, room2)
        self.assertEqual(room2.west, None)

    # Create at

    def test_create_at(self):

        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_CREATE,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save(room=self.room)

        self.room = Room.objects.get(pk=self.room.pk)
        new_room = self.room.east
        self.assertEqual(new_room.west, self.room)


class RoomEditTests(WorldTestCase):

    # Successes

    def test_set_new_type(self):
        self.room.type = adv_consts.ROOM_TYPE_ROAD
        self.room.save()

        serializer = builder_serializers.RoomEditSerializer(
            room=self.room,
            data={
                'attribute': 'type',
                'value': adv_consts.ROOM_TYPE_INDOOR,
            })
        serializer.is_valid(raise_exception=True)
        room = serializer.save()

        self.room.refresh_from_db()
        self.assertEqual(room.type, adv_consts.ROOM_TYPE_INDOOR)

    # Failures

    def test_set_invalid_attribute(self):
        serializer = builder_serializers.RoomEditSerializer(
            room=self.room,
            data={
                'attribute': 'INVALID',
                'value': 'something'
            })
        self.assertFalse(serializer.is_valid())

    def test_set_invalid_type(self):
        serializer = builder_serializers.RoomEditSerializer(
            room=self.room,
            data={
                'attribute': 'type',
                'value': 'INVALID'
            })
        self.assertFalse(serializer.is_valid())


class RoomCheckTests(BuilderTestCase):

    def test_add_room_check(self):
        ep = reverse(
            'builder-room-checks',
            args=[
                self.world.pk,
                self.room.pk])
        resp = self.client.post(ep, {
          'name': "Unnamed Check",
          'prevent': "exit",
          'check': "equipped",
          'direction': "",
          'argument': "",
          'argument2': ""
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        check = RoomCheck.objects.get(pk=resp.data['id'])
        self.assertEqual(check.room, self.room)


class RoomDetailTests(BuilderTestCase):

    def test_add_room_detail(self):
        list_ep = reverse(
            'builder-room-detail-list', args=[self.world.pk, self.room.pk])
        resp = self.client.post(list_ep, {
          'keywords': 'rock',
          'description': 'It is a big rock.'
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        detail = RoomDetail.objects.get(pk=resp.data['id'])
        self.assertEqual(detail.room, self.room)

    def test_edit_room_detail(self):
        detail = RoomDetail.objects.create(
            room=self.room,
            keywords='thing',
            description='It is a thing')
        resp = self.client.put(
            reverse(
            'builder-room-detail-detail',
            args=[self.world.pk, self.room.pk, detail.pk]),
            {
              'keywords': 'someTHING or other',
              'description': 'It is something.'
            }, format='json')
        self.assertEqual(resp.status_code, 200)
        detail.refresh_from_db()
        self.assertEqual(detail.keywords, 'something')
        self.assertEqual(detail.description, 'It is something.')


class RoomFlagsTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.ep = reverse('builder-room-flags', args=[
            self.world.pk,
            self.room.pk])

    def test_get_room_flags(self):
        resp = self.client.get(self.ep)
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(len(resp.data), 4)
        self.assertFalse(resp.data[0]['value'])
        self.assertFalse(resp.data[1]['value'])

        RoomFlag.objects.create(
            code=adv_consts.ROOM_FLAG_NO_ROAM,
            room=self.room)
        resp = self.client.get(self.ep)
        self.assertTrue(resp.data[0]['value'])

    def test_toggle_room_flag(self):
        RoomFlag.objects.create(
            code=adv_consts.ROOM_FLAG_NO_ROAM,
            room=self.room)
        ep = reverse('builder-room-flag-toggle', args=[
            self.world.pk,
            self.room.pk,
            adv_consts.ROOM_FLAG_NO_ROAM,
        ])
        resp = self.client.post(ep)
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(resp.data['value'])
        with self.assertRaises(RoomFlag.DoesNotExist):
            self.room.flags.get(code=adv_consts.ROOM_FLAG_NO_ROAM)

        resp = self.client.post(ep)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data['value'])
        RoomFlag.objects.get(room_id=self.room.pk,
                             code=adv_consts.ROOM_FLAG_NO_ROAM)


class RoomActionTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.list_ep = reverse(
            'builder-room-action-list',
            args=[self.world.pk, self.room.pk])

    def test_add_room_action(self):
        # Minimum add
        resp = self.client.post(self.list_ep, {
            'actions': 'pull lever',
            'commands': 'transfer {{ actor }} 1',
        })
        self.assertEqual(resp.status_code, 201)
        action = RoomAction.objects.get(pk=resp.data['id'])
        self.assertEqual(action.room, self.room)

        # Full add
        resp = self.client.post(self.list_ep, {
            'name': 'pull lever',
            'actions': 'pull lever',
            'commands': 'transfer {{ actor }} 1',
            'conditions': 'level_above 1',
            'show_details_on_failure': True,
            'failure_message': "It's too heavy",
        })

    def test_validate_conditions(self):
        # Invalid condition
        resp = self.client.post(self.list_ep, {
            'actions': 'pull lever',
            'commands': 'transfer {{ actor }} 1',
            'conditions': 'above_level 1',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['conditions'][0],
            "Invalid condition name 'above_level'")

        # Invalid argument count
        resp = self.client.post(self.list_ep, {
            'actions': 'pull lever',
            'commands': 'transfer {{ actor }} 1',
            'conditions': 'level_above',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['conditions'][0],
            "Insufficient number of arguments to 'level_above'")

        # Invalid second argument
        resp = self.client.post(self.list_ep, {
            'actions': 'pull lever',
            'commands': 'transfer {{ actor }} 1',
            'conditions': 'level_above 1 or below_level 3',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['conditions'][0],
            "Invalid condition name 'below_level'")

    def test_validate_commands(self):
        # Invalid command
        resp = self.client.post(self.list_ep, {
            'actions': 'pull lever',
            'commands': 'bash {{ actor }} 1',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['commands'][0], "Invalid room command 'bash'")

        # commands with newlines
        resp = self.client.post(self.list_ep, {
            'actions': 'pull lever',
            'commands': "send {{ actor }} You'd be going\nbash",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['commands'][0], "Invalid room command 'bash'")

        # commands with &&
        resp = self.client.post(self.list_ep, {
            'actions': 'pull lever',
            'commands': "send {{ actor }} You'd be going && bash",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['commands'][0], "Invalid room command 'bash'")

        # commands with ;
        resp = self.client.post(self.list_ep, {
            'actions': 'pull lever',
            'commands': "send {{ actor }} You'd be going;bash",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['commands'][0], "Invalid room command 'bash'")

    def test_validate_take_command(self):
        "Regression test that the /take command's qty is optional"
        resp = self.client.post(self.list_ep, {
            'actions': 'take from player',
            'commands': '/take apple {{ actor }}'
        })
        self.assertEqual(resp.status_code, 201)


class RoomColorValidationTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.detail_endpoint = reverse('builder-room-detail',
                                       args=[self.world.pk, self.room.pk])

    def test_edit_room_with_color(self):
        self.assertIsNone(self.room.color)
        data = {
            'color': 'red',
        }
        resp = self.client.put(self.detail_endpoint, data)
        self.assertEqual(resp.status_code, 200)
        room = Room.objects.get(pk=self.room.pk)
        self.assertEqual(room.color, 'red')

    def test_color_validation(self):
        zone = Zone.objects.create(world=self.world, name='Awesome zone')
        data = {
            'color': 'something ; < not standard',
        }
        resp = self.client.put(self.detail_endpoint, data)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['color'][0], 'Invalid color value.')


class ItemActionTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.item_template = ItemTemplate.objects.create(
            name='a rock',
            world=self.world)
        self.list_ep = reverse(
            'builder-item-action-list',
            args=[self.world.pk, self.item_template.pk])

    def test_add_item_action(self):
        # Minimum add
        resp = self.client.post(self.list_ep, {
            'actions': 'pull lever',
            'commands': 'transfer {{ actor }} 1',
        })
        self.assertEqual(resp.status_code, 201)
        action = ItemAction.objects.get(pk=resp.data['id'])
        self.assertEqual(action.item_template, self.item_template)

        # Full add
        resp = self.client.post(self.list_ep, {
            'name': 'pull lever',
            'actions': 'pull lever',
            'commands': 'transfer {{ actor }} 1',
            'conditions': 'level_above 1',
            'show_details_on_failure': True,
            'failure_message': "It's too heavy",
        })


class DoorTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.room2 = Room.objects.create(
            world=self.world,
            x=1, y=0, z=0)
        self.room.east = self.room2
        self.room.save()

        self.room2.west = self.room
        self.room2.save()

        self.set_room_ep = endpoint = reverse(
            'builder-room-set-door',
            args=[self.world.pk, self.room.pk])

    def test_add_door(self):
        "Simplest case"
        resp = self.client.post(self.set_room_ep, {
            'direction': 'east',
            'name': 'door',
        })
        self.assertEqual(resp.status_code, 201)

        # Door was created
        door = Door.objects.get(
            from_room=self.room,
            to_room=self.room2)
        self.assertEqual(door.direction, 'east')

        # Reverse door also
        door = Door.objects.get(
            from_room=self.room2,
            to_room=self.room)
        self.assertEqual(door.direction, 'west')

    def test_add_door_with_options(self):
        key = ItemTemplate.objects.create(
            type='key',
            name='a key',
            world=self.world)
        resp = self.client.post(self.set_room_ep, {
            'name': 'dooR dropped', # 'dropped' will be dropped
            'default_state': 'locked',
            'direction': 'east',
            'key': key.key,
            'destroy_key': True,
        })
        self.assertEqual(resp.status_code, 201)

        door = Door.objects.get(
            from_room=self.room,
            to_room=self.room2)
        self.assertEqual(door.name, 'door')
        self.assertEqual(door.default_state, 'locked')
        self.assertEqual(door.key, key)
        self.assertTrue(door.destroy_key)

        # Door on the other side has the same name & default state
        door2 = Door.objects.get(
            from_room=self.room2,
            to_room=self.room)
        self.assertEqual(door2.name, 'door')
        self.assertEqual(door2.default_state, 'locked')
        self.assertEqual(door2.key, key)
        self.assertTrue(door2.destroy_key)

    def test_add_door_to_one_way(self):
        "For one-way, reverse door does not get created."
        self.room2.west = None
        self.room2.save()

        resp = self.client.post(self.set_room_ep, {
            'direction': 'east',
            'name': 'door',
        })
        self.assertEqual(resp.status_code, 201)

        # Door was created
        door = Door.objects.get(
            from_room=self.room,
            to_room=self.room2)
        self.assertEqual(door.direction, 'east')

        with self.assertRaises(Door.DoesNotExist):
            # Reverse door does not
            door = Door.objects.get(
                from_room=self.room2,
                to_room=self.room)

    def test_change_connection_to_one_way_alters_door(self):
        door1 = Door.objects.create(
            from_room=self.room,
            to_room=self.room2,
            direction='east')
        door2 = Door.objects.create(
            from_room=self.room2,
            to_room=self.room,
            direction='west')
        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_ONE_WAY,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save()
        # Door1 is still there
        door1.refresh_from_db()
        with self.assertRaises(Door.DoesNotExist):
            door2.refresh_from_db() # door 2 is gone

    def test_change_connection_to_two_way_removes_one_door(self):
        self.room2.west = None
        self.room2.save()
        door1 = Door.objects.create(
            from_room=self.room,
            to_room=self.room2,
            direction='east')
        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_MUTUAL,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save(room=self.room)
        with self.assertRaises(Door.DoesNotExist):
            door1.refresh_from_db()

    def test_remove_connection_removes_door2(self):
        door1 = Door.objects.create(
            from_room=self.room,
            to_room=self.room2,
            direction='east')
        door2 = Door.objects.create(
            from_room=self.room2,
            to_room=self.room,
            direction='west')
        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_NO_EXIT,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save()
        with self.assertRaises(Door.DoesNotExist):
            door1.refresh_from_db()
        with self.assertRaises(Door.DoesNotExist):
            door2.refresh_from_db()

    def test_set_existing_door(self):
        door = Door.objects.create(
            from_room=self.room,
            to_room=self.room2,
            name='door',
            direction='east',
            destroy_key=False)
        ep = reverse('builder-room-set-door',
                     args=[self.world.pk, self.room.pk])
        resp = self.client.post(ep, {
            'direction': 'east',
            'name': 'gate',
            'destroy_key': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201)

        door.refresh_from_db()
        self.assertEqual(door.name, 'gate')
        self.assertTrue(door.destroy_key)

    def test_asymmetrical_door_states(self):
        "Test one side locked and the other closed"
        key = ItemTemplate.objects.create(
            type='key',
            name='a key',
            world=self.world)
        door = Door.objects.create(
            from_room=self.room,
            to_room=self.room2,
            name='door',
            direction='east',
            default_state='locked',
            destroy_key=True,
            key=key)
        door2 = Door.objects.create(
            from_room=self.room2,
            to_room=self.room,
            name='door',
            direction='west',
            default_state='locked')

        ep = reverse('builder-room-set-door',
                     args=[self.world.pk, self.room.pk])
        resp = self.client.post(ep, {
            'direction': 'east',
            'name': 'door',
            'default_state': 'closed',
            'key': None,
            'destroy_key': False,
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        door.refresh_from_db()
        self.assertEqual(door.default_state, 'closed')
        self.assertIsNone(door.key)
        self.assertFalse(door.destroy_key)

    def test_clear_door_mutual(self):
        door1 = Door.objects.create(
            from_room=self.room,
            to_room=self.room2,
            direction='east')
        door2 = Door.objects.create(
            from_room=self.room2,
            to_room=self.room,
            direction='west')
        ep = reverse('builder-room-clear-door',
                     args=[self.world.pk, self.room.pk])
        resp = self.client.post(ep, {'direction': 'east'})
        self.assertEqual(resp.status_code, 204)

        # Door 1 is gone
        with self.assertRaises(Door.DoesNotExist):
            door1.refresh_from_db()

        # Door 2 is gone
        with self.assertRaises(Door.DoesNotExist):
            door2.refresh_from_db()

    def test_clear_door_one_way(self):
        self.room2.west = None
        self.room2.save()
        door = Door.objects.create(
            from_room=self.room,
            to_room=self.room2,
            direction='east')
        ep = reverse('builder-room-clear-door',
                     args=[self.world.pk, self.room.pk])
        resp = self.client.post(ep, {'direction': 'east'})
        self.assertEqual(resp.status_code, 204)

        # Door is gone
        with self.assertRaises(Door.DoesNotExist):
            door.refresh_from_db()

        # There is no other door
        self.assertEqual(Door.objects.count(), 0)

    def test_delete_room_deletes_doors(self):
        door1 = Door.objects.create(
            from_room=self.room,
            to_room=self.room2,
            direction='east')
        door2 = Door.objects.create(
            from_room=self.room2,
            to_room=self.room,
            direction='west')

        resp = self.client.delete(
            reverse('builder-room-detail', args=[self.world.pk, self.room.pk]))
        self.assertEqual(resp.status_code, 204)

        with self.assertRaises(Door.DoesNotExist):
            door1.refresh_from_db()

        with self.assertRaises(Door.DoesNotExist):
            door2.refresh_from_db()

    # Room manipulation tests

    def test_disconnect_removes_door(self):
        door1 = Door.objects.create(
            from_room=self.room,
            to_room=self.room2)
        door2 = Door.objects.create(
            from_room=self.room2,
            to_room=self.room)

        serializer = builder_serializers.RoomDirActionSerializer(
            room=self.room,
            data={
                'action': adv_consts.EXIT_ACTION_NO_EXIT,
                'direction': 'east',
            })
        if serializer.is_valid(raise_exception=True):
            serializer.save()

        with self.assertRaises(Door.DoesNotExist):
            Door.objects.get(pk=door1.pk)

        with self.assertRaises(Door.DoesNotExist):
            Door.objects.get(pk=door2.pk)

    def test_deleting_a_room_removes_doors(self):
        door1 = Door.objects.create(
            from_room=self.room,
            to_room=self.room2)
        door2 = Door.objects.create(
            from_room=self.room2,
            to_room=self.room)

        resp = self.client.delete(
            reverse('builder-room-detail',
                    args=[self.world.pk, self.room.pk]))
        self.assertEqual(resp.status_code, 204)

        with self.assertRaises(Door.DoesNotExist):
            Door.objects.get(pk=door1.pk)

        with self.assertRaises(Door.DoesNotExist):
            Door.objects.get(pk=door2.pk)

    def test_set_exit(self):
        "If we set the exit of a room to a new room, the door should update."
        door1 = Door.objects.create(
            from_room=self.room,
            to_room=self.room2,
            direction='east')
        door2 = Door.objects.create(
            from_room=self.room2,
            to_room=self.room,
            direction='west')

        room3 = Room.objects.create(
            world=self.world,
            x=1, y=1, z=0)

        resp = self.client.put(
            reverse('builder-room-detail', args=[self.world.pk, self.room.pk]),
            {
                'east': room3.key,
            })
        self.assertEqual(resp.status_code, 200)
        self.room.refresh_from_db()
        self.assertEqual(self.room.east, room3)

        door1.refresh_from_db()
        self.assertEqual(door1.to_room, room3)

        # door 2 is unchanged
        door2.refresh_from_db()
        self.assertEqual(door2.to_room, self.room)

    # Validation tests

    def test_direction_is_required(self):
        resp = self.client.post(self.set_room_ep, {})
        self.assertEqual(resp.status_code, 400)

    def test_cannot_set_door_if_no_connection(self):
        self.room.east = None
        self.room.save()
        resp = self.client.post(self.set_room_ep, {
            'direction': 'east',
        })
        self.assertEqual(resp.status_code, 400)

    # Regression tests

    def test_add_spw_locked_door(self):
        self.assertFalse(self.world.is_multiplayer)
        spawn_world = self.world.create_spawn_world()
        resp = self.client.post(self.set_room_ep, {
            'direction': 'east',
            'name': 'door',
            'default_state': 'locked',
        })
        self.assertEqual(resp.status_code, 201)

        # Door was created
        door = Door.objects.get(
            from_room=self.room,
            to_room=self.room2)
        self.assertEqual(door.direction, 'east')

        # Door state was created
        state = DoorState.objects.get(door=door, world=spawn_world)
        self.assertEqual(state.state, 'locked')


class MobTemplateTests(BuilderTestCase):

    def test_create_mob_template(self):
        endpoint = reverse('builder-mob-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {'name': 'a spider'})
        self.assertEqual(resp.status_code, 201)
        template = MobTemplate.objects.get(pk=resp.data['id'])
        self.assertEqual(template.name, 'a spider')
        self.assertEqual(template.world, self.world)

        # Test use of suggested stats for non-provided values
        for stat, value in suggest_stats(level=1).items():
            if stat in ('mana_base', 'health_base', 'stamina_base'):
                continue
            self.assertEqual(getattr(template, stat), value)

        # Since on initial creation we don't input stats in the frontend,
        # we automatically enable suggested stats.
        self.assertTrue(template.default_stats)

    def test_humanoid_default_gold(self):
        "Tests that when creating a humanoid, they drop gold by default."
        endpoint = reverse('builder-mob-template-list', args=[self.world.pk])

        # validate that non-humanoids don't drop any
        resp = self.client.post(endpoint, {
            'name': 'a spider',
            'type': adv_consts.MOB_TYPE_BEAST,
        })
        self.assertEqual(resp.status_code, 201)
        spider_template = MobTemplate.objects.get(pk=resp.data['id'])
        self.assertEqual(spider_template.type, adv_consts.MOB_TYPE_BEAST)
        self.assertEqual(spider_template.gold, 0)

        # humanoids do
        resp = self.client.post(endpoint, {
            'name': 'a soldier',
            'type': adv_consts.MOB_TYPE_HUMANOID,
            'level': 2,
        })
        self.assertEqual(resp.status_code, 201)
        soldier_template = MobTemplate.objects.get(pk=resp.data['id'])
        self.assertEqual(soldier_template.gold,
                         round(adv_config.ILF(soldier_template.level)))

    def test_edit_mob_template(self):
        mob_template = MobTemplate.objects.create(name='a soldier',
                                                  world=self.world,
                                                  health_max=1)
        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])
        resp = self.client.put(endpoint, {
            'name': 'a bandit',
            'health_max': 15,
        }, format='json')
        self.assertEqual(resp.status_code, 200)

        mob_template.refresh_from_db()
        self.assertEqual(mob_template.name, 'a bandit')
        self.assertEqual(mob_template.health_max, 15)

    def test_delete_mob_template(self):
        mob_template = MobTemplate.objects.create(name='a soldier',
                                                  world=self.world)
        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 204)
        with self.assertRaises(MobTemplate.DoesNotExist):
            mob_template.refresh_from_db()

    def test_default_stats(self):
        from core.utils.mobs import suggest_stats

        mob_template = MobTemplate.objects.create(name='a soldier',
                                                  world=self.world,
                                                  health_max=1)
        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])
        resp = self.client.put(endpoint, {
            'name': 'a bandit',
            'health_max': 2,
            'default_stats': False,
        }, format='json')
        self.assertEqual(resp.status_code, 200)

        mob_template.refresh_from_db()
        self.assertFalse(mob_template.default_stats)
        self.assertEqual(mob_template.health_max, 2)

        resp = self.client.put(endpoint, {
            'name': 'a bandit',
            'health_max': 2,
            'default_stats': True,
        }, format='json')
        self.assertEqual(resp.status_code, 200)

        mob_template.refresh_from_db()
        self.assertTrue(mob_template.default_stats)
        health_max = suggest_stats(level=mob_template.level)['health_max']
        self.assertEqual(mob_template.health_max, health_max)

    def test_elite_multiplier(self):
        from core.utils.mobs import suggest_stats

        mob_template = MobTemplate.objects.create(name='a soldier',
                                                  world=self.world,
                                                  health_max=1)
        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])
        resp = self.client.put(endpoint, {
            'name': 'a bandit',
            'health_max': 2,
            'default_stats': True,
            'is_elite': True,
        }, format='json')
        self.assertEqual(resp.status_code, 200)

        mob_template.refresh_from_db()
        health_max = suggest_stats(level=mob_template.level)['health_max']
        boosted_health = suggest_stats(
            level=mob_template.level, is_elite=True)['health_max']
        self.assertEqual(mob_template.health_max, boosted_health)
        self.assertEqual(boosted_health, health_max * adv_config.ELITE_BOOST['health_max'])

    def test_cannot_delete_template_that_has_loaded_mobs(self):
        spawned_world = self.world.create_spawn_world()
        mob_template = MobTemplate.objects.create(name='a soldier',
                                                  world=self.world)
        mob = mob_template.spawn(target=self.room, spawn_world=spawned_world)
        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 400)

    def test_mob_template_level_cap(self):
        endpoint = reverse('builder-mob-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'name': 'a spider',
            'level': 21,
        })
        self.assertEqual(resp.status_code, 400)

    def test_null_out_mob_faction(self):
        "Regression test for being able to set a mob to null value"
        default_core_faction = Faction.objects.create(
            code='a',
            name='Faction A',
            world=self.world,
            is_core=True,
            is_default=True,
            is_selectable=True)

        # Create new mob
        endpoint = reverse('builder-mob-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'name': 'a spider',
            'core_faction': 'a'
        })
        self.assertEqual(resp.status_code, 201)
        mob_template_id = resp.data['id']
        self.assertTrue(
            FactionAssignment.objects.filter(
                faction=default_core_faction,
                member_id=mob_template_id).exists())

        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template_id])
        resp = self.client.put(endpoint, {
            'core_faction': '',
        }, format='json')
        self.assertEqual(resp.status_code, 200)

        self.assertFalse(
            FactionAssignment.objects.filter(
                faction=default_core_faction,
                member_id=mob_template_id).exists())

    def test_switch_mob_faction(self):
        a_faction = Faction.objects.create(
            code='a',
            name='Faction A',
            world=self.world,
            is_core=True,
            is_default=True,
            is_selectable=True)

        b_faction = Faction.objects.create(
            code='b',
            name='Faction B',
            world=self.world,
            is_core=True)

        # Create new mob
        endpoint = reverse('builder-mob-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'name': 'a spider',
            'core_faction': 'a'
        })
        self.assertEqual(resp.status_code, 201)
        mob_template_id = resp.data['id']
        self.assertTrue(
            FactionAssignment.objects.filter(
                faction=a_faction,
                member_id=mob_template_id).exists())

        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template_id])
        resp = self.client.put(endpoint, {
            'core_faction': 'b',
        }, format='json')
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(
            FactionAssignment.objects.filter(
                member_id=mob_template_id).count(),
            1)
        self.assertEqual(
            FactionAssignment.objects.filter(
                member_id=mob_template_id).get().faction,
            b_faction)

    def test_identical_mob_faction(self):
        "Regression test for bug with dupe faction assignments"
        a_faction = Faction.objects.create(
            code='a',
            name='Faction A',
            world=self.world,
            is_core=True,
            is_default=True,
            is_selectable=True)

        # Create new mob
        endpoint = reverse('builder-mob-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'name': 'a spider',
            'core_faction': 'a'
        })
        self.assertEqual(resp.status_code, 201)
        mob_template_id = resp.data['id']
        self.assertTrue(
            FactionAssignment.objects.filter(
                faction=a_faction,
                member_id=mob_template_id).exists())

        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template_id])
        resp = self.client.put(endpoint, {
            'core_faction': 'a',
        }, format='json')
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(
            FactionAssignment.objects.filter(
                member_id=mob_template_id).count(),
            1)
        self.assertEqual(
            FactionAssignment.objects.filter(
                member_id=mob_template_id).get().faction,
            a_faction)

class MobTemplateInventoryTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.mob_template = MobTemplate.objects.create(world=self.world)
        self.item_template = ItemTemplate.objects.create(world=self.world)
        self.ep = reverse('builder-mob-template-inventory',
                          args=[self.world.pk, self.mob_template.key])

    def test_list_mob_template_inventory(self):
        template_inventory = MobTemplateInventory.objects.create(
            container=self.mob_template,
            item_template=self.item_template)

        resp = self.client.get(self.ep)
        self.assertEqual(resp.status_code, 200)
        data = resp.data['data']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['probability'], 100)
        self.assertEqual(data[0]['item_template']['key'],
                         self.item_template.key)

    def test_create_mob_template_inventory(self):
        resp = self.client.post(self.ep, {
            'probability': '80',
            'num_copies': '2',
            'item_template': { 'key': self.item_template.key }
        }, format='json')
        self.assertEqual(resp.status_code, 201)

        mti = MobTemplateInventory.objects.get(pk=resp.data['id'])
        self.assertEqual(mti.container, self.mob_template)
        self.assertEqual(mti.item_template, self.item_template)
        self.assertEqual(mti.probability, 80)
        self.assertEqual(mti.num_copies, 2)

        # Rest defaults
        resp = self.client.post(self.ep, {
            'item_template': { 'key': self.item_template.key }
        }, format='json')
        mti = MobTemplateInventory.objects.get(pk=resp.data['id'])
        self.assertEqual(mti.probability, 100)
        self.assertEqual(mti.num_copies, 1)

    def test_edit_mob_template_inventory(self):
        template_inventory = MobTemplateInventory.objects.create(
            container=self.mob_template,
            item_template=self.item_template,
            probability=20)
        ep = reverse(
            'builder-mob-template-inventory-detail',
            args=[self.world.pk, self.mob_template.pk, template_inventory.pk])

        resp = self.client.put(ep, {
            'probability': 30,
            'item_template': {'key': self.item_template.key}
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        template_inventory.refresh_from_db()
        self.assertEqual(template_inventory.probability, 30)

    def test_delete_mob_template_inventory(self):
        template_inventory = MobTemplateInventory.objects.create(
            container=self.mob_template,
            item_template=self.item_template,
            probability=20)
        ep = reverse(
            'builder-mob-template-inventory-detail',
            args=[self.world.pk, self.mob_template.pk, template_inventory.pk])
        resp = self.client.delete(ep)
        self.assertEqual(resp.status_code, 204)
        with self.assertRaises(MobTemplateInventory.DoesNotExist):
            MobTemplateInventory.objects.get(pk=template_inventory.pk)


class MobTemplateMerchantInventoryTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.mob_template = MobTemplate.objects.create(world=self.world)
        self.item_template = ItemTemplate.objects.create(world=self.world)
        self.random_item_profile = RandomItemProfile.objects.create(
            world=self.world)
        self.list_ep = reverse(
            'builder-mob-template-merchant-inventory-list',
            args=[self.world.pk, self.mob_template.id])

    def test_add_inventory(self):
        resp = self.client.post(self.list_ep, {
            'random_item_profile': {'key': self.random_item_profile.key},
            'num': 2,
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        merchant_inventory = MerchantInventory.objects.get(
            pk=resp.data['id'])
        self.assertEqual(
            merchant_inventory.random_item_profile,
            self.random_item_profile)
        self.assertEqual(merchant_inventory.num, 2)

        resp = self.client.post(self.list_ep, {
            'item_template': {'key': self.item_template.key},
            'num': 2,
        }, format='json')
        self.assertEqual(resp.status_code, 201)

    def test_add_validation(self):
        resp = self.client.post(self.list_ep, {'num_copies': 1})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['non_field_errors'],
            ['Either an item template or a random profile is required.'])

        resp = self.client.post(self.list_ep, {
            'random_item_profile': {'key': self.random_item_profile.key},
            'item_template': {'key': self.item_template.key},
            'num': 2,
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('not both', resp.data['non_field_errors'][0])


class ItemTemplateTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.collection_ep = reverse('builder-item-template-list',
                                     args=[self.world.pk])

    def test_create_item_template(self):
        endpoint = reverse('builder-item-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {'name': 'a torch'})
        self.assertEqual(resp.status_code, 201)
        template = ItemTemplate.objects.get(pk=resp.data['id'])
        self.assertEqual(template.name, 'a torch')
        self.assertEqual(template.world, self.world)

    def test_equipment_type_for_non_eq(self):
        """
        Tests that equipment type gets discarded if the item is not
        equipment.
        """
        resp = self.client.post(self.collection_ep, {
            'name': 'a thing',
            'type': adv_consts.ITEM_TYPE_INERT,
            'equipment_type': adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
        })
        self.assertEqual(resp.status_code, 201)
        template = ItemTemplate.objects.get(pk=resp.data['id'])
        self.assertIsNone(template.equipment_type)

        detail_ep = reverse('builder-item-template-detail',
                            args=[self.world.pk, template.pk])
        resp = self.client.put(detail_ep, {
            'name': 'a different thing',
            'equipment_type': adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
        })
        self.assertEqual(resp.status_code, 200)
        template.refresh_from_db()
        self.assertIsNone(template.equipment_type)

    def test_edit_item_template(self):
        item_template = ItemTemplate.objects.create(name='a rock',
                                                    world=self.world)
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        resp = self.client.put(endpoint, {'name': 'a stone'})
        self.assertEqual(resp.status_code, 200)
        item_template.refresh_from_db()
        self.assertEqual(item_template.name, 'a stone')

    def test_delete_item_template(self):
        item_template = ItemTemplate.objects.create(name='a rock',
                                                    world=self.world)
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 204)

    def test_cannot_delete_template_that_has_loaded_items(self):
        spawned_world = self.world.create_spawn_world()
        item_template = ItemTemplate.objects.create(name='a rock',
                                                    world=self.world)
        item = item_template.spawn(target=self.room, spawn_world=spawned_world)
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 400)

    def test_setting_attributes_sets_is_magic(self):
        item_template = ItemTemplate.objects.create(
            name='a rock',
            world=self.world,
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H)
        self.assertEqual(item_template.quality, adv_consts.ITEM_QUALITY_NORMAL)

        # Boosting strength marks the item as Imbued
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        resp = self.client.put(endpoint, {
            'strength': 10,
        })
        self.assertEqual(resp.status_code, 200)
        item_template.refresh_from_db()
        self.assertEqual(item_template.quality, adv_consts.ITEM_QUALITY_IMBUED)

        # Boosting strength to a ridiculous amount marks it as Enchanted
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        resp = self.client.put(endpoint, {
            'strength': 1000000,
        })
        self.assertEqual(resp.status_code, 200)
        item_template.refresh_from_db()
        self.assertEqual(item_template.quality, adv_consts.ITEM_QUALITY_ENCHANTED)

        # Setting it back to 0 returns the quality to normal
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        resp = self.client.put(endpoint, {
            'strength': 0,
        })
        self.assertEqual(resp.status_code, 200)
        item_template.refresh_from_db()
        self.assertEqual(item_template.quality, adv_consts.ITEM_QUALITY_NORMAL)

    def test_cannot_set_persistent_item_that_is_not_pickable(self):

        # ==== Creation ====

        resp = self.client.post(
            reverse('builder-item-template-list', args=[self.world.pk]),
            {
                'name': 'a torch',
                'is_persistent': True,
                'is_pickable': True,
            })
        self.assertEqual(resp.status_code, 400)

        # Success case
        resp = self.client.post(
            reverse('builder-item-template-list', args=[self.world.pk]),
            {
                'name': 'a torch',
                'is_persistent': True,
                'is_pickable': False,
            })
        self.assertEqual(resp.status_code, 201)

        # ==== Editing ====

        item_template = ItemTemplate.objects.create(name='a rock',
                                                    world=self.world,
                                                    is_pickable=True)
        resp = self.client.put(
            reverse('builder-item-template-detail',
                    args=[self.world.pk, item_template.pk]),
            {
                'name': 'a stone',
                'is_persistent': True,
                'is_pickable': True,
            })
        self.assertEqual(resp.status_code, 400)

        # Success case
        item_template = ItemTemplate.objects.create(name='a rock',
                                                    world=self.world,
                                                    is_persistent=False)
        resp = self.client.put(
            reverse('builder-item-template-detail',
                    args=[self.world.pk, item_template.pk]),
            {
                'name': 'a stone',
                'is_persistent': True,
                'is_pickable': False
            })
        self.assertEqual(resp.status_code, 200)

    # Instance list tests
    def test_list_instance_item_templates(self):
        item_template = ItemTemplate.objects.create(name='a rock',
                                                    world=self.world)

        # Item shows up in base world list
        resp = self.client.get(
            reverse('builder-item-template-list', args=[self.world.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['count'], 1)
        self.assertEqual(resp.json()['results'][0]['key'], item_template.key)

        # Item also shows up in instance list
        instance_context = self.create_instance()
        resp = self.client.get(
            reverse('builder-item-template-list', args=[instance_context.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['count'], 1)
        self.assertEqual(resp.json()['results'][0]['key'], item_template.key)


class ItemTemplateInventoryTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.container = ItemTemplate.objects.create(
            world=self.world,
            type=adv_consts.ITEM_TYPE_CONTAINER)
        self.item_template = ItemTemplate.objects.create(world=self.world)
        self.ep = reverse('builder-item-template-inventory',
                          args=[self.world.pk, self.container.key])

    def test_list_item_template_inventory(self):
        template_inventory = ItemTemplateInventory.objects.create(
            container=self.container,
            item_template=self.item_template)

        resp = self.client.get(self.ep)
        self.assertEqual(resp.status_code, 200)
        data = resp.data['data']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['probability'], 100)
        self.assertEqual(data[0]['item_template']['key'],
                         self.item_template.key)

    def test_create_item_template_inventory(self):
        resp = self.client.post(self.ep, {
            'probability': '80',
            'num_copies': '2',
            'item_template': { 'key': self.item_template.key }
        }, format='json')
        self.assertEqual(resp.status_code, 201)

        iti = ItemTemplateInventory.objects.get(pk=resp.data['id'])
        self.assertEqual(iti.container, self.container)
        self.assertEqual(iti.item_template, self.item_template)
        self.assertEqual(iti.probability, 80)
        self.assertEqual(iti.num_copies, 2)

        # Rest defaults
        resp = self.client.post(self.ep, {
            'item_template': { 'key': self.item_template.key }
        }, format='json')
        iti = ItemTemplateInventory.objects.get(pk=resp.data['id'])
        self.assertEqual(iti.probability, 100)
        self.assertEqual(iti.num_copies, 1)

    def test_cannot_add_to_non_container_template(self):
        self.container.type = adv_consts.ITEM_TYPE_INERT
        self.container.save()
        resp = self.client.post(self.ep, {
            'item_template': { 'key': self.item_template.key }
        }, format='json')

        self.assertEqual(resp.status_code, 400)

    def test_edit_item_template_inventory(self):
        template_inventory = ItemTemplateInventory.objects.create(
            container=self.container,
            item_template=self.item_template,
            probability=20)
        ep = reverse(
            'builder-item-template-inventory-detail',
            args=[self.world.pk, self.container.pk, template_inventory.pk])

        resp = self.client.put(ep, {
            'probability': 30,
            'item_template': {'key': self.container.key}
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        template_inventory.refresh_from_db()
        self.assertEqual(template_inventory.probability, 30)

    def test_cannot_set_contained_container_to_non_container(self):
        "Tests that a container can't be 'turned off' if it has things."
        template_inventory = ItemTemplateInventory.objects.create(
            container=self.container,
            item_template=self.item_template,
            probability=20)
        ep = reverse(
            'builder-item-template-detail',
            args=[self.world.pk, self.container.pk])
        resp = self.client.put(ep, {
            'type': adv_consts.ITEM_TYPE_INERT,
        }, format='json')
        self.assertEqual(resp.status_code, 400)

        # Deleting the inventory solves the issue
        template_inventory.delete()
        ep = reverse(
            'builder-item-template-detail',
            args=[self.world.pk, self.container.pk])
        resp = self.client.put(ep, {
            'type': adv_consts.ITEM_TYPE_INERT,
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.container.refresh_from_db()
        self.assertEqual(self.container.type, adv_consts.ITEM_TYPE_INERT)

    def test_delete_item_template_inventory(self):
        template_inventory = ItemTemplateInventory.objects.create(
            container=self.container,
            item_template=self.item_template,
            probability=20)
        ep = reverse(
            'builder-item-template-inventory-detail',
            args=[self.world.pk, self.container.pk, template_inventory.pk])
        resp = self.client.delete(ep)
        self.assertEqual(resp.status_code, 204)
        with self.assertRaises(ItemTemplateInventory.DoesNotExist):
            ItemTemplateInventory.objects.get(pk=template_inventory.pk)

    def test_spawn_template_inventory(self):
        spawn_world = self.world.create_spawn_world()
        template_inventory = ItemTemplateInventory.objects.create(
            container=self.container,
            item_template=self.item_template,
            num_copies=2)
        container = self.container.spawn(
            target=self.room, spawn_world=self.world)
        self.assertEqual(len(container.inventory.all()), 2)


class ItemTemplatePricingTests(BuilderTestCase):

    def test_container_pricing(self):
        item_template = ItemTemplate.objects.create(name='a rock',
                                                    world=self.world)
        bag_template = ItemTemplate.objects.create(
            name='a bag',
            world=self.world,
            type=adv_consts.ITEM_TYPE_CONTAINER)

        ItemTemplateInventory.objects.create(
            item_template=item_template,
            container=bag_template)

        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, bag_template.pk])
        resp = self.client.put(endpoint, {
            'name': 'a bag',
            'level': 1,
            'type': 'container',
            'cost': '10',
            'notes': '',
        })
        self.assertEqual(resp.status_code, 200)


class LoaderTests(BuilderTestCase):

    def test_create_loader(self):
        endpoint = reverse('builder-loader-list', args=[self.world.pk])
        data = {
            'name': 'Unnamed loader',
            'respawn_wait': '1',
            'zone': self.zone.key,
            'description': '',
        }

        resp = self.client.post(endpoint, data=data)
        self.assertEqual(resp.status_code, 201)
        loader = Loader.objects.get(pk=resp.data['id'])
        self.assertEqual(loader.world, self.world)
        self.assertEqual(loader.zone, self.zone)

    def test_add_rule(self):
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(world=self.world)

        # Test that the loader pk has to be valid
        self.assertEqual(
            self.client.post(
                reverse('builder-loader-rule-list',
                        args=[self.world.pk, 99]),
                {}).status_code, 404)

        endpoint = reverse(
            'builder-loader-rule-list',
            args=[self.world.pk, loader.pk])

        resp = self.client.post(endpoint, data={
            'template': item_template.key,
            'target': self.room.key,
        })
        self.assertEqual(resp.status_code, 201)
        rule = Rule.objects.get(pk=resp.data['id'])
        self.assertEqual(rule.template, item_template)
        self.assertEqual(rule.target, self.room)
        self.assertEqual(rule.order, 1)

        # Test ordering (and also using a mob template instead)
        mob_template = MobTemplate.objects.create(world=self.world)
        resp = self.client.post(endpoint, data={
            'template': mob_template.key,
            'target': None,
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        rule = Rule.objects.get(pk=resp.data['id'])
        self.assertEqual(rule.template, mob_template)
        self.assertIsNone(rule.target)
        self.assertEqual(rule.order, 2)

    def test_add_zone_rule(self):
        "Tests adding a rule to an entire zone"
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(world=self.world)

        endpoint = reverse(
            'builder-loader-rule-list',
            args=[self.world.pk, loader.pk])

        resp = self.client.post(endpoint, data={
            'template': item_template.key,
            'target': self.zone.key,
        })
        self.assertEqual(resp.status_code, 201)
        rule = Rule.objects.get(pk=resp.data['id'])
        self.assertEqual(rule.template, item_template)
        self.assertEqual(rule.target, self.zone)
        self.assertEqual(rule.order, 1)

    def test_add_path_rule(self):
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(world=self.world)
        path = Path.objects.create(world=self.world, zone=self.zone)
        endpoint = reverse(
            'builder-loader-rule-list',
            args=[self.world.pk, loader.pk])


        resp = self.client.post(endpoint, data={
            'template': item_template.key,
            'target': path.key,
        })
        self.assertEqual(resp.status_code, 201)
        rule = Rule.objects.get(pk=resp.data['id'])
        self.assertEqual(rule.target, path)

    def test_add_transformation_template_rule(self):
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        mob_template = MobTemplate.objects.create(world=self.world)

        rule1 = Rule.objects.create(
            loader=loader,
            template=mob_template,
            target=self.room)

        transformation_template = TransformationTemplate.objects.create(
            transformation_type=api_consts.TRANSFORMATION_TYPE_ATTR,
            arg1='roams',
            arg2='east')

        endpoint = reverse(
            'builder-loader-rule-list',
            args=[self.world.pk, loader.pk])

        resp = self.client.post(endpoint, data={
            'template': transformation_template.key,
            'target': rule1.key,
        })
        self.assertEqual(resp.status_code, 201)

        # Failure test: make sure a transformation only targets previous
        # rules
        resp = self.client.post(endpoint, data={
            'template': transformation_template.key,
            'target': self.room.key,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['non_field_errors'][0],
            'Transformation Templates can only target rules.')

    def test_edit_rule(self):
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(world=self.world)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room,
            num_copies=2)
        ep = reverse('builder-loader-rule-detail',
                     args=[self.world.pk, loader.pk, rule.pk])
        resp = self.client.put(ep, {'num_copies': 3})
        self.assertEqual(resp.status_code, 200)

        rule.refresh_from_db()
        self.assertEqual(rule.num_copies, 3)

    def test_cannot_edit_rule_with_other_world_template(self):
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(world=self.world)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        other_world = World.objects.new_world(
            name='Other World',
            author=self.user)
        other_template = ItemTemplate.objects.create(world=other_world)

        ep = reverse('builder-loader-rule-detail',
                     args=[self.world.pk, loader.pk, rule.pk])
        resp = self.client.put(ep, {'template': other_template.key})
        self.assertEqual(resp.status_code, 400)
        rule.refresh_from_db()
        self.assertEqual(rule.template, item_template)

    def test_delete_rule(self):
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(world=self.world)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room)

        resp = self.client.delete(
            reverse('builder-loader-rule-detail',
                    args=[self.world.pk, loader.pk, rule.pk]))
        self.assertEqual(resp.status_code, 204)

        with self.assertRaises(Rule.DoesNotExist):
            Rule.objects.get(pk=rule.pk)
        # Make sure the references still exist
        Loader.objects.get(pk=loader.pk)
        ItemTemplate.objects.get(pk=item_template.pk)
        Room.objects.get(pk=self.room.pk)

    def test_rule_room_must_belong_to_zone(self):
        """
        Regression test. Tests that a rule cannot target a room that does
        not belong the loader's zone.
        """
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        mob_template = MobTemplate.objects.create(world=self.world)
        ep = reverse('builder-loader-rule-list',
                     args=[self.world.pk, loader.pk])

        zone2 = Zone.objects.create(world=self.world)
        room2 = self.room.create_at('east')
        room2.zone = zone2
        room2.save()

        # Test that the loader zone has to be valid to create
        resp = self.client.post(
            ep, {
                'template': mob_template.key,
                'target': room2.key,
            })
        self.assertEqual(resp.status_code, 400)
        self.assertIsNotNone(resp.data['target'])

        # As well as to edit
        rule = Rule.objects.create(loader=loader,
                                   template=mob_template,
                                   target=self.room)
        detail_ep = reverse('builder-loader-rule-detail',
                            args=[self.world.pk, loader.pk, rule.pk])
        resp = self.client.put(
            detail_ep, {
                'target': room2.key,
            })
        self.assertEqual(resp.status_code, 400)
        self.assertIsNotNone(resp.data['target'])

    def test_rule_only_one_quest_mob_copy(self):
        """
        Tests that when saving a rule targetting a quest mob, we don't
        add more than 1 instance of it throughout the whole world.
        """
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        mob_template = MobTemplate.objects.create(world=self.world)
        quest = Quest.objects.create(
            world=self.world,
            mob_template=mob_template)

        ep = reverse('builder-loader-rule-list',
                     args=[self.world.pk, loader.pk])
        resp = self.client.post(
            ep, {
                'template': mob_template.key,
                'target': self.room.key,
            })
        self.assertEqual(resp.status_code, 201)

        rule = loader.rules.first()

        # Try to add a second rule for the same mob
        ep = reverse('builder-loader-rule-list',
                     args=[self.world.pk, loader.pk])
        resp = self.client.post(ep, {
            'template': mob_template.key,
            'target': self.room.key,
        })
        self.assertEqual(resp.status_code, 400)

        # A normal edit works
        other_room = self.room.create_at('east')
        ep = reverse('builder-loader-rule-detail',
                     args=[self.world.pk, loader.pk, rule.pk])
        resp = self.client.put(
            ep, {
                'target': other_room.key
            })
        self.assertEqual(resp.status_code, 200)

        # Bumping the number of copies up to 2 doesn't work
        ep = reverse('builder-loader-rule-detail',
                     args=[self.world.pk, loader.pk, rule.pk])
        resp = self.client.put(
            ep, {
                'num_copies': 2,
            })
        self.assertEqual(resp.status_code, 400)

    def test_rule_target_must_belong_to_loader(self):
        """
        Tests that when adding a rule that targets another rule, that
        targeted rule must belong to the same loader.
        """
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(world=self.world)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room,
            num_copies=2)

        loader2 = Loader.objects.create(world=self.world, zone=self.zone)

        endpoint = reverse(
            'builder-loader-rule-list',
            args=[self.world.pk, loader2.pk])

        resp = self.client.post(endpoint, data={
            'template': item_template.key,
            'target': rule.key,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['target'][0],
                         'Rule target does not belong to loader.')

    def test_cannot_add_persistent_items(self):
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(
            world=self.world,
            is_persistent=True)

        endpoint = reverse(
            'builder-loader-rule-list',
            args=[self.world.pk, loader.pk])
        resp = self.client.post(endpoint, data={
            'template': item_template.key,
            'target': self.zone.key,
        })
        self.assertEqual(resp.status_code, 400)

    def test_cannot_add_persistent_item_load_via_room_load(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            is_persistent=True)
        endpoint = reverse('builder-room-loads', args=[self.world.pk, self.room.pk])
        resp = self.client.post(endpoint, data={
            'template': item_template.key,
        })
        self.assertEqual(resp.status_code, 400)

    def test_cannot_set_loaded_template_to_persistent(self):
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(
            world=self.world,
            is_persistent=False)
        rule = Rule.objects.create(
            loader=loader,
            template=item_template,
            target=self.room,
            num_copies=1)
        ep = reverse('builder-item-template-detail',
                     args=[self.world.pk, item_template.pk])
        resp = self.client.put(ep, {'is_persistent': True})
        self.assertEqual(resp.status_code, 400)

    def test_add_rule_target(self):
        "Load a mob with an item in their inventory"

        mob_template = MobTemplate.objects.create(world=self.world)
        loader = Loader.objects.create(world=self.world, zone=self.zone)

        endpoint = reverse(
            'builder-loader-rule-list',
            args=[self.world.pk, loader.pk])

        resp = self.client.post(endpoint, data={
            'template': mob_template.key,
            'target': self.room.key,
        })
        self.assertEqual(resp.status_code, 201)
        rule = Rule.objects.get(pk=resp.data['id'])
        self.assertEqual(rule.template, mob_template)
        self.assertEqual(rule.target, self.room)
        self.assertEqual(rule.order, 1)

        # Try to add a second rule targetting the output of the first

        # Test ordering (and also using a mob template instead)
        item_template = ItemTemplate.objects.create(world=self.world)
        resp = self.client.post(endpoint, data={
            'template': item_template.key,
            'target': rule.key,
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        rule2 = Rule.objects.get(pk=resp.data['id'])
        self.assertEqual(rule2.template, item_template)
        self.assertEqual(rule2.target, rule)
        self.assertEqual(rule2.order, 2)

    def test_rule_cannot_target_self(self):
        """
        Regression for trying to target a rule with the output of another
        rule.
        """
        mob_template = MobTemplate.objects.create(world=self.world)
        loader = Loader.objects.create(world=self.world, zone=self.zone)

        endpoint = reverse(
            'builder-loader-rule-list',
            args=[self.world.pk, loader.pk])

        resp = self.client.post(endpoint, data={
            'template': mob_template.key,
            'target': self.room.key,
        })
        self.assertEqual(resp.status_code, 201)
        rule = Rule.objects.get(pk=resp.data['id'])
        self.assertEqual(rule.template, mob_template)
        self.assertEqual(rule.target, self.room)
        self.assertEqual(rule.order, 1)

        # Now try setting the target to the rule itself
        ep = reverse('builder-loader-rule-detail',
                     args=[self.world.pk, loader.pk, rule.pk])
        resp = self.client.put(ep, {'target': rule.key})
        self.assertEqual(resp.status_code, 400)

    def test_rule_cannot_load_mob_into_mob(self):
        """
        Regression test that a loader cannot attempt to load a mob into
        another mob.
        """
        mob_template = MobTemplate.objects.create(world=self.world)
        mob_template2 = MobTemplate.objects.create(world=self.world)
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        rule = Rule.objects.create(loader=loader,
                                   template=mob_template,
                                   target=self.room)

        endpoint = reverse(
            'builder-loader-rule-list',
            args=[self.world.pk, loader.pk])

        resp = self.client.post(endpoint, data={
            'template': mob_template2.key,
            'target': rule.key,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['non_field_errors'][0],
            "Mob template rule cannot target the output of another rule.")

    # Instance tests
    def test_rule_in_instance(self):
        instance_context = self.create_instance()
        instance_zone = instance_context.zones.all()[0]
        instance_room = instance_zone.rooms.all()[0]
        loader = Loader.objects.create(world=instance_context,
                                       zone=instance_zone)
        item_template = ItemTemplate.objects.create(world=self.world)

        endpoint = reverse(
            'builder-loader-rule-list',
            args=[instance_context.pk, loader.pk])

        resp = self.client.post(endpoint, data={
            'template': item_template.key,
            'target': instance_room.key,
        })
        self.assertEqual(resp.status_code, 201)
        rule = Rule.objects.get(pk=resp.data['id'])
        self.assertEqual(rule.template, item_template)
        self.assertEqual(rule.target, instance_room)


class CeilingTests(BuilderTestCase):
    """
    Tests that try and limit some of the damage that crazy
    builders could do to the system.
    """

    def test_max_copies(self):
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        item_template = ItemTemplate.objects.create(world=self.world)

        # Cannot create a rule with more than the max number of
        # spawns
        list_ep = reverse('builder-loader-rule-list',
                          args=[self.world.pk, loader.pk])
        resp = self.client.post(list_ep, {
            'template': item_template.key,
            'target': self.room.key,
            'num_copies': api_consts.MAX_RULE_SPAWNS + 1
        })
        self.assertEqual(resp.status_code, 400)

        # Creating one with less than is fine
        list_ep = reverse('builder-loader-rule-list',
                          args=[self.world.pk, loader.pk])
        resp = self.client.post(list_ep, {
            'template': item_template.key,
            'target': self.room.key,
            'num_copies': api_consts.MAX_RULE_SPAWNS - 1
        })
        self.assertEqual(resp.status_code, 201)

        rule = Rule.objects.get(pk=resp.data['id'])

        ep = reverse('builder-loader-rule-detail',
                     args=[self.world.pk, loader.pk, rule.pk])
        resp = self.client.put(ep, {
            'num_copies': api_consts.MAX_RULE_SPAWNS + 1
        })
        self.assertEqual(resp.status_code, 400)


class TestAddRoomLoad(BuilderTestCase):
    "Tests adding loads directly to a room"

    def test_add_mob_load_to_room(self):
        ep = reverse('builder-room-loads', args=[self.world.pk, self.room.pk])
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name="a soldier")
        self.room.name = "the battle field"
        self.room.save()

        resp = self.client.post(ep, {
            'template': mob_template.key
        })
        self.assertEqual(resp.status_code, 201)
        loader = Loader.objects.get(pk=resp.data['id'])
        self.assertEqual(loader.name, "a soldier in the battle field")
        rule = loader.rules.get()
        self.assertEqual(rule.template, mob_template)
        self.assertEqual(rule.target, self.room)


class MobReactionTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.mob_template = MobTemplate.objects.create(world=self.world)
        self.ep = reverse('builder-mob-template-reactions',
                          args=[self.world.pk, self.mob_template.key])

    def _reaction_triggers(self):
        return Trigger.objects.filter(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobTemplate),
            target_id=self.mob_template.id,
        ).order_by('id')

    def test_add_mob_reaction(self):
        resp = self.client.post(self.ep, {
            'event': 'enter',
            'reaction': 'say hi!',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._reaction_triggers().count(), 1)
        trigger = self._reaction_triggers().first()
        self.assertEqual(trigger.kind, adv_consts.TRIGGER_KIND_EVENT)
        self.assertEqual(trigger.event, adv_consts.MOB_REACTION_EVENT_ENTERING)
        self.assertEqual(trigger.script, 'say hi!')

        # Test that passing blank option works too
        resp = self.client.post(self.ep, {
            'event': 'enter',
            'reaction': 'say hi!',
            'option': '',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._reaction_triggers().count(), 2)

    def test_add_mob_reaction_with_condition(self):
        "Regression test for adding a mob reaction that has a condition."
        resp = self.client.post(self.ep, {
            'event': 'enter',
            'reaction': 'say hi!',
            'conditions': 'is_mob',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._reaction_triggers().first().conditions,
                         'is_mob')

    def test_option_is_required_for_say(self):
        resp = self.client.post(self.ep, {
            'event': 'say',
            'reaction': 'say hi!',
        }, format='json')
        self.assertEqual(resp.status_code, 400)


class PathTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.path = Path.objects.create(
            zone=self.zone,
            world=self.world)
        self.path_rooms_ep = reverse(
            'builder-path-rooms',
            args=[self.world.pk, self.path.pk])

    @mock.patch('builders.models.Path.update_live_instances')
    def test_create_path(self, mock_update_live_instances):
        self.assertEqual(Path.objects.count(), 1)
        create_ep = reverse('builder-zone-path-list', args=[
            self.world.pk,
            self.zone.pk])
        resp = self.client.post(create_ep, {
            'name': 'New Path'
        })
        self.assertEqual(Path.objects.count(), 2)
        new_path = Path.objects.get(pk=resp.data['id'])
        self.assertEqual(new_path.name, 'New Path')
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(mock_update_live_instances.called)

    @mock.patch('builders.views.Path.update_live_instances')
    def test_add_room(self, mock_update_live_instances):
        self.assertEqual(self.path.rooms.count(), 0)

        resp = self.client.post(self.path_rooms_ep, {
            'room': {'key': self.room.key},
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.path.rooms.count(), 1)
        self.assertTrue(mock_update_live_instances.called)

        # Trying to add another raises a 409
        resp = self.client.post(self.path_rooms_ep, {
            'room': {'key': self.room.key},
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    @mock.patch('builders.views.Path.update_live_instances')
    def test_remove_room(self, mock_update_live_instances):
        path_room = PathRoom.objects.create(
            path=self.path,
            room=self.room)
        self.assertEqual(self.path.rooms.count(), 1)
        resp = self.client.delete(
            reverse('builder-path-room-detail',
            args=[self.world.pk, self.path.pk, path_room.pk]))
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self.path.rooms.count(), 0)
        self.assertTrue(mock_update_live_instances.called)


class MobFactionTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.mob_template = MobTemplate.objects.create(world=self.world)
        self.faction = Faction.objects.create(
            world=self.world,
            code='orc',
            name='Orc')
        self.faction_assignment = FactionAssignment.objects.create(
            faction=self.faction,
            value=100,
            member_type=ContentType.objects.get_for_model(self.mob_template),
            member_id=self.mob_template.id)

        self.ep = reverse(
            'builder-mob-template-faction-detail',
            args=[
                self.world.pk,
                self.mob_template.key,
                self.faction_assignment.pk])

    def test_add_faction_assignment(self):
        n_faction = Faction.objects.create(
            world=self.world,
            code='nak',
            name="Nak'Rosh")
        ep = reverse('builder-mob-template-factions',
                     args=[self.world.pk, self.mob_template.key])
        resp = self.client.post(ep, {
            'faction': {'key': n_faction.key},
            'value': '200',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        qs = FactionAssignment.objects.filter(
            member_id=self.mob_template.id,
            member_type=ContentType.objects.get_for_model(self.mob_template))
        self.assertEqual(qs.count(), 2)
        self.assertEqual(
            self.mob_template.faction_assignments.last().faction.id,
            n_faction.id)

    def test_edit_faction_assignment(self):
        resp = self.client.put(self.ep, {
            'faction': {'key': self.faction.key},
            'value': '200',
        }, format='json')
        self.assertEqual(resp.status_code, 200)

    def test_delete_faction_assignment(self):
        resp = self.client.delete(self.ep)
        self.assertEqual(resp.status_code, 204)

    def test_cannot_add_multiple_core_faction_assignments(self):
        """
        Regression test to make sure that another core faction cannot
        be added to a mob, including if it's a duplicate.
        """
        faction = Faction.objects.create(
            world=self.world,
            is_core=True,
            code='core_faction',
            name='Core Faction')
        FactionAssignment.objects.create(
            member_id=self.mob_template.id,
            member_type=ContentType.objects.get_for_model(self.mob_template),
            faction=faction)
        ep = reverse('builder-mob-template-factions',
                     args=[self.world.pk, self.mob_template.key])
        resp = self.client.post(ep, {
            'is_core': True,
            'faction': {'key': faction.key},
            'value': '200',
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_model_prevents_multiple_core_assignments_for_player(self):
        human = Faction.objects.create(
            world=self.world,
            is_core=True,
            code='human',
            name='Human')
        orc = Faction.objects.create(
            world=self.world,
            is_core=True,
            code='orc',
            name='Orc')
        FactionAssignment.objects.create(
            member=self.player,
            faction=human)

        with self.assertRaises(DjangoValidationError):
            FactionAssignment.objects.create(
                member=self.player,
                faction=orc)

    def test_model_prevents_duplicate_member_faction_assignments(self):
        with self.assertRaises(DjangoValidationError):
            FactionAssignment.objects.create(
                member=self.mob_template,
                faction=self.faction,
                value=200)

    def test_add_non_core_faction_after_core(self):
        """
        Regression tests for adding a non-core faction to a mob
        template that has a core faction associated with it (which
        should be fine).
        """
        core = Faction.objects.create(
            world=self.world,
            code='core',
            name='Core Faction',
            is_core=True)

        minor = Faction.objects.create(
            world=self.world,
            code='minor',
            name='Minor Faction',
            is_core=False)

        mob_template = MobTemplate.objects.create(world=self.world)
        core_assignment = FactionAssignment.objects.create(
            faction=core,
            member=mob_template)

        ep = reverse('builder-mob-template-factions',
                     args=[self.world.pk, mob_template.key])
        resp = self.client.post(ep, {
            'faction': {'key': minor.key},
            'value': '200',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        qs = FactionAssignment.objects.filter(
            member_id=mob_template.id,
            member_type=ContentType.objects.get_for_model(mob_template))
        self.assertEqual(qs.count(), 2)


    def test_create_mob_template_with_core_faction(self):
        core_faction = Faction.objects.create(
            world=self.world,
            code='core_faction',
            name='Core Faction',
            is_core=True)

        endpoint = reverse('builder-mob-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'name': 'a soldier',
            'core_faction': core_faction.code,
        })
        self.assertEqual(resp.status_code, 201)
        template = MobTemplate.objects.get(pk=resp.data['id'])
        self.assertEqual(template.faction_assignments.count(), 1)

        # Add test that creating the wrong core value raises a 400
        resp = self.client.post(endpoint, {
            'name': 'a soldier',
            'core_faction': 'bs',
        })
        self.assertEqual(resp.status_code, 400)

    def test_edit_mob_template_core_faction(self):
        human_faction = Faction.objects.create(
            world=self.world,
            code='human',
            name='Human',
            is_core=True)
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a soldier')
        FactionAssignment.objects.create(
            faction=human_faction,
            member=mob_template,
            value=1)

        orc_faction = Faction.objects.create(
            world=self.world,
            code='orc',
            name='Orc',
            is_core=True)

        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])
        resp = self.client.put(endpoint, {
            'name': 'an orc',
            'core_faction': orc_faction.code
        })
        self.assertEqual(resp.status_code, 200)
        mob_template.refresh_from_db()
        self.assertEqual(
            mob_template.faction_assignments.get(
                faction__is_core=True).faction.code,
            orc_faction.code)
        self.assertEqual(mob_template.faction_assignments.count(), 1)

        # Add test that creating the wrong core value raises a 400
        resp = self.client.put(endpoint, {
            'name': 'an orc',
            'core_faction': 'bs'
        })
        self.assertEqual(resp.status_code, 400)


class QuestTests(BuilderTestCase):

    def setUp(self, *args, **kwargs):
        super().setUp(*args, **kwargs)
        self.mob_template = MobTemplate.objects.create(world=self.world)

    def test_create_quest_from_zone_endpoint(self):
        ep = reverse('builder-zone-quest_list',
                     args=[self.world.pk, self.zone.pk])
        resp = self.client.post(ep, {
            'name': 'Test Quest',
            'mob_template': {'key': self.mob_template.key}
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        quest = Quest.objects.first()
        self.assertEqual(quest.name, 'Test Quest')

    def test_create_quest_from_world_endpoint(self):
        ep = reverse('builder-quest-list', args=[self.world.pk])
        resp = self.client.post(ep, {
            'name': 'Test Quest',
            'mob_template': {'key': self.mob_template.key},
            'zone': {'key': self.zone.key},
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        quest = Quest.objects.first()
        self.assertEqual(quest.name, 'Test Quest')

    def test_edit_quest(self):
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        ep = reverse('builder-quest-detail', args=[self.world.pk, quest.pk])
        resp = self.client.put(ep, {
                'requires_quest': None,
                'repeatable_after': '-1',
            }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['repeatable_after'], -1)

    def test_set_quest_prereq(self):
        pre_quest = Quest.objects.create(world=self.world,
                                         mob_template=self.mob_template)
        quest = Quest.objects.create(world=self.world,
                                         mob_template=self.mob_template)
        ep = reverse('builder-quest-detail', args=[self.world.pk, quest.pk])
        resp = self.client.put(ep, {
            'requires_quest': {'key': pre_quest.key},
            'repeatable_after': 2,
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        quest = Quest.objects.get(pk=resp.data['id'])
        self.assertEqual(quest.repeatable_after, 2)
        self.assertEqual(quest.requires_quest, pre_quest)

    def test_cannot_prereq_yourself(self):
        "Tests that a quest cannot have itself as a pre-req"
        quest = Quest.objects.create(world=self.world,
                                         mob_template=self.mob_template)
        ep = reverse('builder-quest-detail', args=[self.world.pk, quest.pk])
        resp = self.client.put(ep, {
            'requires_quest': {'key': quest.key},
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('own requirement', resp.data['requires_quest'][0])

    def test_add_objective(self):
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        ep = reverse('builder-objective-list',
                     args=[self.world.pk, quest.pk])

        # Test without a template
        resp = self.client.post(ep, {
            'type': 'gold',
            'qty': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        objective = quest.objectives.first()
        self.assertEqual(objective.type, 'gold')
        self.assertEqual(objective.qty, 1)

        # Test with a template
        item_template = ItemTemplate.objects.create(world=self.world)
        resp = self.client.post(ep, {
            'type': 'item',
            'qty': 4,
            'template': {'key': item_template.key}
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        objective = Objective.objects.get(pk=resp.data['id'])
        self.assertEqual(objective.template, item_template)
        self.assertEqual(objective.qty, 4)

    def test_edit_quest_objective(self):
        item_template = ItemTemplate.objects.create(world=self.world)
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        objective = Objective.objects.create(
            type='item',
            quest=quest,
            qty=4,
            template_type=ContentType.objects.get_for_model(item_template),
            template_id=item_template.id)

        new_item_template = ItemTemplate.objects.create(world=self.world)

        ep = reverse(
            'builder-objective-detail',
            args=[self.world.pk, objective.key])

        resp = self.client.put(ep, {
            'type': 'item',
            'qty': 3,
            'template': {'key': new_item_template.key}
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        objective.refresh_from_db()
        self.assertEqual(objective.qty, 3)
        self.assertEqual(objective.template, new_item_template)

    def test_cannot_assign_quest_to_mob_loaded_multiple_times(self):
        duplicate_mob = MobTemplate.objects.create(world=self.world)
        loader = Loader.objects.create(world=self.world, zone=self.zone)
        rule = Rule.objects.create(
            loader=loader,
            template=duplicate_mob,
            target=self.room,
            num_copies=2)

         # Can't create new quest with that mob
        ep = reverse('builder-quest-list', args=[self.world.pk])
        resp = self.client.post(ep, {
            'name': 'Test Quest',
            'mob_template': {'key': duplicate_mob.key},
            'zone': {'key': self.zone.key},
        }, format='json')
        self.assertEqual(resp.status_code, 400)

         # Also can't edit a quest setting it to that mob
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        ep = reverse('builder-quest-detail', args=[self.world.pk, quest.pk])
        resp = self.client.put(ep, {
                'mob_template': duplicate_mob.key,
            }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_regression_faction_standing_reward(self):
        """
        Regression test for trying to add faction reward while not passing
        a faction as the profile.
        """
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        ep = reverse('builder-reward-list',
                     args=[self.world.pk, quest.pk])

        # Test without a faction
        resp = self.client.post(ep, {
            'type': adv_consts.REWARD_TYPE_FACTION,
            'qty': 10,
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_objective_validation(self):
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        ep = reverse('builder-objective-list',
                     args=[self.world.pk, quest.pk])
        # Test without a template
        resp = self.client.post(ep, {
            'type': adv_consts.OBJECTIVE_TYPE_ITEM,
            'qty': 1,
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_cannot_reward_non_core_faction(self):
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        ep = reverse('builder-reward-list',
                     args=[self.world.pk, quest.pk])
        core_faction = Faction.objects.create(
            world=self.world,
            is_core=True,
            code='core_faction',
            name='Core Faction')

        resp = self.client.post(ep, {
            'type': 'faction',
            'profile': {'key': core_faction.key},
        }, format='json')
        self.assertEqual(resp.status_code, 400)

        non_core_faction = Faction.objects.create(
            world=self.world,
            is_core=False,
            code='non_core_faction',
            name='Non Core Faction')

        resp = self.client.post(ep, {
            'type': 'faction',
            'profile': {'key': non_core_faction.key},
        }, format='json')
        self.assertEqual(resp.status_code, 201)

    def test_delete_quest_mob_template(self):
        "Test that a mob template used by a quest can't be deleted."
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        endpoint = reverse('builder-mob-template-detail',
                            args=[self.world.pk, self.mob_template.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                        'Cannot delete a template used for a quest.')

    def test_delete_objective_item_template(self):
        "Test that an item template used in an objective can't be deleted."
        item_template = ItemTemplate.objects.create(world=self.world)
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        objective = Objective.objects.create(
            quest=quest,
            type='item',
            qty=1,
            template_type=ContentType.objects.get_for_model(ItemTemplate),
            template_id=item_template.id)
        endpoint = reverse('builder-item-template-detail',
                            args=[self.world.pk, item_template.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                            'Cannot delete a template used for a quest objective.')

    def test_delete_objective_mob_template(self):
        "Test that a mob template used in an objective can't be deleted."
        mob_template = MobTemplate.objects.create(world=self.world)
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        objective = Objective.objects.create(
            quest=quest,
            type='mob',
            qty=1,
            template_type=ContentType.objects.get_for_model(MobTemplate),
            template_id=mob_template.id)
        endpoint = reverse('builder-mob-template-detail',
                            args=[self.world.pk, mob_template.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                            'Cannot delete a template used for a quest objective.')

    def test_delete_item_template_used_in_reward(self):
        """
        Regression test that the Forge shouldn't let a user delete a template
        that is being used by a quest reward.
        """
        item_template = ItemTemplate.objects.create(world=self.world)
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        Reward.objects.create(
            quest=quest,
            profile_type=ContentType.objects.get_for_model(ItemTemplate),
            profile_id=item_template.id)
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                         'Cannot delete a template used for a quest reward.')

    def test_delete_item_profile_used_in_reward(self):
        item_profile = RandomItemProfile.objects.create(world=self.world)
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        Reward.objects.create(
            quest=quest,
            profile_type=ContentType.objects.get_for_model(RandomItemProfile),
            profile_id=item_profile.id)
        endpoint = reverse('builder-random-item-profile-detail',
                           args=[self.world.pk, item_profile.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                         'Cannot delete a profile used for a quest reward.')

    def test_delete_faction_standing_used_in_reward(self):
        faction = Faction.objects.create(
            world=self.world,
            code='faction',
            name='Faction',
            is_core=False)
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        Reward.objects.create(
            quest=quest,
            profile_type=ContentType.objects.get_for_model(Faction),
            profile_id=faction.id)
        endpoint = reverse('builder-world-faction-detail',
                            args=[self.world.pk, faction.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                         'Cannot delete a faction used for a quest reward.')

    # Currencies

    def test_add_currency_objective(self):
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        ep = reverse('builder-objective-list',
                     args=[self.world.pk, quest.pk])

        currency = Currency.objects.create(
            code='coins',
            name='Coins',
            world=self.world)

        resp = self.client.post(ep, {
            'type': 'currency',
            'currency': currency.id,
            'qty': 10,
        })
        self.assertEqual(resp.status_code, 201)

        objective = quest.objectives.first()
        self.assertEqual(objective.type, 'currency')
        self.assertEqual(objective.currency, currency)

    def test_edit_currency_objective(self):
        item_template = ItemTemplate.objects.create(world=self.world)
        quest = Quest.objects.create(
            world=self.world,
            mob_template=self.mob_template)
        objective = Objective.objects.create(
            type='gold',
            quest=quest,
            qty=10)

        currency = Currency.objects.create(
            code='coins',
            name='Coins',
            world=self.world)

        ep = reverse(
            'builder-objective-detail',
            args=[self.world.pk, objective.key])

        resp = self.client.put(ep, {
            'type': 'currency',
            'qty': 20,
            'currency': currency.id,
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        objective.refresh_from_db()
        self.assertEqual(objective.qty, 20)
        self.assertEqual(objective.currency, currency)


class WorldBuildersTests(BuilderTestCase):

    def test_add_builder(self):
        new_user = User.objects.create_user('new@example.com', 'p')
        ep = reverse('builder-builder-list', args=[self.world.pk])
        resp = self.client.post(ep, {
            'user': {'key': new_user.key}
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        world_builder = WorldBuilder.objects.get(pk=resp.data['id'])
        self.assertEqual(world_builder.user, new_user)
        self.assertEqual(world_builder.builder_rank, 1)

        # Trying to add that same user again returns a 400
        resp = self.client.post(ep, {
            'user': {'key': new_user.key}
        }, format='json')
        self.assertEqual(resp.status_code, 400)

        resp = self.client.post(ep, {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_edit_builder_access(self):
        new_user = User.objects.create_user('new@example.com', 'p')
        world_builder = WorldBuilder.objects.create(
            world=self.world,
            user=new_user)
        self.assertEqual(world_builder.read_only, True)

        ep = reverse('builder-builder-detail',
                     args=[self.world.pk, world_builder.pk])
        resp = self.client.put(ep, {
            'user': {'key': new_user.key},
            'read_only': False,
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        world_builder.refresh_from_db()
        self.assertEqual(world_builder.read_only, False)

    def test_read_permission_enforcement(self):
        new_user = User.objects.create_user('new@example.com', 'p')
        self.client.force_authenticate(new_user)
        resp = self.client.get(reverse('builder-room-list', args=[self.world.id]))
        self.assertEqual(resp.status_code, 403)

    def test_write_permission_enforcement(self):
        read_only_builder_user = User.objects.create_user('new@example.com', 'p')
        WorldBuilder.objects.create(
            world=self.world,
            user=read_only_builder_user,
            builder_rank=1)
        self.client.force_authenticate(read_only_builder_user)
        item_template = ItemTemplate.objects.create(name='a rock',
                                                    world=self.world)
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        resp = self.client.put(endpoint, {'name': 'a stone'})
        self.assertEqual(resp.status_code, 403)


class WorldFactionTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.factions_ep = reverse('builder-world-factions', args=[
            self.world.pk])

    def test_list_factions(self):
        resp = self.client.get(self.factions_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'], [])

        faction = Faction.objects.create(
            code='myfaction',
            name='My Faction',
            world=self.world,
            is_core=True)

        resp = self.client.get(self.factions_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['id'], faction.pk)

    def test_add_core_faction(self):
        resp = self.client.post(self.factions_ep, {
            'code': 'myfaction',
            'name': 'My Faction',
            'is_core': True,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Faction.objects.count(), 1)

    def test_add_non_core_faction(self):
        resp = self.client.post(self.factions_ep, {
            'code': 'myfaction',
            'name': 'My Faction',
            'is_core': False,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Faction.objects.count(), 1)
        faction = Faction.objects.get()
        self.assertFalse(faction.is_core)

    def test_faction_code_normalization(self):
        resp = self.client.post(self.factions_ep, {
            'code': 'My FactioN 3',
            'name': 'My Faction',
            'is_core': True,
        })
        self.assertEqual(resp.status_code, 201)
        faction = Faction.objects.get(pk=resp.data['id'])
        self.assertEqual(faction.code, 'my_faction_3')

    def test_prevent_changing_faction_code_with_running_world(self):
        faction = Faction.objects.create(
            world=self.world,
            code='faction',
            name='Faction')
        self.world.is_multiplayer = True
        self.world.save()
        spawn_world = self.world.create_spawn_world(
            lifecycle=api_consts.WORLD_STATE_RUNNING)
        resp = self.client.put(
            reverse(
                'builder-world-faction-detail',
                args=[self.world.pk,faction.pk]),
            {
                'code': 'faction2',
                'name': 'Faction',
            })
        self.assertEqual(resp.status_code, 400)
        spawn_world.lifecycle = api_consts.WORLD_STATE_CLEAN
        spawn_world.save(update_fields=['lifecycle'])
        resp = self.client.put(reverse(
                'builder-world-faction-detail',
                args=[self.world.pk,faction.pk]),
            {
                'code': 'faction2',
                'name': 'Faction',
            })
        self.assertEqual(resp.status_code, 200)
        faction.refresh_from_db()
        self.assertEqual(faction.code, 'faction2')

    def test_edit_faction(self):
        faction = Faction.objects.create(
            code='myfaction',
            name='My Faction',
            world=self.world,
            is_core=True)

        ep = reverse('builder-world-faction-detail', args=[
            self.world.pk,
            faction.pk])
        resp = self.client.put(ep, {
            'name': 'My edited faction',
            'code': 'myeditedfaction',
            'is_core': False,
        })
        self.assertEqual(resp.status_code, 200)
        faction.refresh_from_db()
        self.assertEqual(faction.name, 'My edited faction')
        self.assertEqual(faction.code, 'myeditedfaction')
        self.assertFalse(faction.is_core)

    def test_only_one_default(self):
        faction1 = Faction.objects.create(
            code='myfaction1',
            name='My Faction 1',
            world=self.world,
            is_default=True,
            is_core=True,
            is_selectable=True)
        faction2 = Faction.objects.create(
            code='myfaction2',
            name='My Faction 2',
            world=self.world,
            is_default=False,
            is_core=True,
            is_selectable=True)
        ep = reverse('builder-world-faction-detail', args=[
            self.world.pk, faction2.pk])
        resp = self.client.put(ep, {
            'code': 'myfaction2',
            'name': 'My Faction 2',
            'is_default': True,
            'is_selectable': True,
            'is_core': True,
        })
        self.assertEqual(resp.status_code, 200)
        faction1.refresh_from_db()
        faction2.refresh_from_db()
        self.assertFalse(faction1.is_default)
        self.assertTrue(faction2.is_default)

        Faction.objects.update(is_default=True)
        ep = reverse('builder-world-factions', args=[
            self.world.pk])
        resp = self.client.post(ep, {
            'code': 'myfaction3',
            'name': 'My Faction 3',
            'is_default': True,
            'is_selectable': True,
            'is_core': True,
        })
        self.assertEqual(resp.status_code, 201)
        faction1.refresh_from_db()
        faction2.refresh_from_db()
        self.assertFalse(faction1.is_default)
        self.assertFalse(faction2.is_default)
        faction = Faction.objects.get(pk=resp.data['id'])
        self.assertTrue(faction.is_default)

    def test_default_must_be_selectable(self):
        faction = Faction.objects.create(
            code='myfaction',
            name='My Faction',
            world=self.world,
            is_default=True,
            is_selectable=True)
        ep = reverse('builder-world-faction-detail', args=[
            self.world.pk, faction.pk])

        # Test cannot set default faction to unselectable
        resp = self.client.put(ep, {
            'code': faction.code,
            'name': faction.name,
            'is_selectable': False,
        })
        self.assertEqual(resp.status_code, 400)

        # Test cannot set unselectable faction to default
        faction.is_selectable = False
        faction.is_default = False
        resp = self.client.put(ep, {
            'code': faction.code,
            'name': faction.name,
            'is_default': True,
        })
        self.assertEqual(resp.status_code, 400)

        # Cannot create faction both default and unselectable
        ep = reverse('builder-world-factions', args=[
            self.world.pk])
        resp = self.client.post(ep, {
            'code': 'myfaction2',
            'name': 'My Faction 2',
            'is_default': True,
            'is_selectable': False,
        })
        self.assertEqual(resp.status_code, 400)

    def test_only_core_factions_can_be_default(self):
        "Only core factions should be able to made default"

        # Test creation
        ep = reverse('builder-world-factions', args=[
            self.world.pk])
        resp = self.client.post(ep, {
            'code': 'myfaction',
            'name': 'My Faction',
            'is_core': False,
            'is_default': True,
            'is_selectable': True,
        })
        self.assertEqual(resp.status_code, 400)

        # Test edit
        faction = Faction.objects.create(
            code='myfaction',
            name='My Faction',
            world=self.world,
            is_core=True,
            is_default=True)
        ep = reverse('builder-world-faction-detail', args=[
            self.world.pk, faction.pk])
        resp = self.client.put(ep, {
            'code': faction.code,
            'name': faction.name,
            'is_core': False,
            'is_default': True,
            'is_selectable': True,
        })
        self.assertEqual(resp.status_code, 400)

        faction.is_core = False
        faction.is_default = False
        faction.save()
        resp = self.client.put(ep, {
            'code': faction.code,
            'name': faction.name,
            'is_default': True,
            'is_core': False,
            'is_selectable': True,
        })
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_faction_codes(self):
        "Regression test for duplicate faction codes"
        resp = self.client.post(self.factions_ep, {
            'code': 'myfaction',
            'name': 'My Faction',
            'is_core': False,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Faction.objects.count(), 1)
        faction = Faction.objects.get()
        self.assertFalse(faction.is_core)

        # Can't create a duplicate faction code
        resp = self.client.post(self.factions_ep, {
            'code': 'myfaction',
            'name': 'My Duplicate Faction',
            'is_core': False,
        })
        self.assertEqual(
            resp.data['non_field_errors'][0],
            'A faction with this code already exists.')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Faction.objects.count(), 1)

        # Can't edit a faction to have a duplicate code
        second_faction = Faction.objects.create(
            world=self.world,
            code='myfaction2',
            name='My Faction 2',
            is_core=False)
        ep = reverse('builder-world-faction-detail', args=[
            self.world.pk, second_faction.pk])
        resp = self.client.put(ep, {
            'code': 'myfaction',
            'name': 'My Duplicate Faction',
            'is_core': False,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['non_field_errors'][0],
            'A faction with this code already exists.')
        # But a different new code is fine
        resp = self.client.put(ep, {
            'code': 'myfactiontwo',
            'name': 'My Duplicate Faction',
            'is_core': False,
        })
        self.assertEqual(resp.status_code, 200)
        second_faction.refresh_from_db()
        self.assertEqual(second_faction.code, 'myfactiontwo')

    def test_change_minor_faction_to_core_with_conflict(self):
        """
        Regression test for following workflow:
        - Player has faction A as core faction
        - Player has faction B as minor faction
        - Builder changes faction B to core

        This would create a situation where the player has two core
        factions, which is not allowed.
        """
        faction_a = Faction.objects.create(
            code='faction_a',
            name='Faction A',
            world=self.world,
            is_core=True)
        faction_b = Faction.objects.create(
            code='faction_b',
            name='Faction B',
            world=self.world,
            is_core=False)

        self.spawned_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            name='John',
            room=self.room,
            user=self.user,
            world=self.spawned_world)

        FactionAssignment.objects.create(faction=faction_a,
                                         member=self.player)
        FactionAssignment.objects.create(faction=faction_b,
                                         member=self.player)

        ep = reverse('builder-world-faction-detail', args=[
            self.world.pk,
            faction_b.pk])
        resp = self.client.put(ep, {
            'code': 'faction_b',
            'name': 'Faction B',
            'is_core': True,
        })

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data['non_field_errors'][0],
            "Cannot change to core faction when characters with this "
            "faction already have a core faction.")

    def test_delete_faction(self):
        faction = Faction.objects.create(
            world=self.world,
            code='faction',
            name='Faction')
        endpoint = reverse('builder-world-faction-detail', args=[
            self.world.pk, faction.pk])
        resp = self.client.delete(endpoint, args=[self.world.pk, faction.pk])
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(Faction.objects.count(), 0)

    def test_cannot_delete_core_faction_in_use(self):
        faction = Faction.objects.create(
            world=self.world,
            code='faction',
            name='Faction',
            is_core=True)
        FactionAssignment.objects.create(
            member=self.player,
            faction=faction)
        endpoint = reverse('builder-world-faction-detail', args=[
            self.world.pk, faction.pk])
        resp = self.client.delete(endpoint, args=[self.world.pk, faction.pk])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Faction.objects.count(), 1)

    def test_can_delete_a_minor_faction_with_assigments(self):
        faction = Faction.objects.create(
            world=self.world,
            code='faction',
            name='Faction',
            is_core=False)
        FactionAssignment.objects.create(
            member=self.player,
            faction=faction)
        endpoint = reverse('builder-world-faction-detail', args=[
            self.world.pk, faction.pk])
        resp = self.client.delete(endpoint, args=[self.world.pk, faction.pk])
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(Faction.objects.count(), 0)


class WorldFactionRankTests(BuilderTestCase):

    def setUp(self):
        super().setUp()

        faction = Faction.objects.create(
            code='templar',
            name='Templar',
            world=self.world,
            is_core=False)
        self.faction = faction

        self.faction_ranks_ep = reverse(
            'builder-world-faction-rank-list',
            args=[self.world.pk, faction.pk])

    def test_list_rank_factions(self):
        resp = self.client.get(self.faction_ranks_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'], [])

        faction_rank = FactionRank.objects.create(
            faction=self.faction,
            standing=100,
            name='Recruit')

        resp = self.client.get(self.faction_ranks_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['id'], faction_rank.id)

    def test_list_rank_add(self):
        resp = self.client.post(self.faction_ranks_ep, {
            'standing': 100,
            'name': 'Recruit',
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(FactionRank.objects.count(), 1)

    def test_rank_edit(self):
        faction_rank = FactionRank.objects.create(
            faction=self.faction,
            standing=100,
            name='Recruit')
        ep = reverse('builder-world-faction-rank-detail', args=[
            self.world.id,
            self.faction.id,
            faction_rank.id])
        resp = self.client.patch(ep, {
            'standing' : 110,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['standing'], 110)
        faction_rank.refresh_from_db()
        self.assertEqual(faction_rank.standing, 110)


class WorldManagePlayerTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.spawned_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            name='John',
            room=self.room,
            user=self.user,
            world=self.spawned_world)

    def test_set_builder(self):
        """
        Regression test for setting a player to be a builder character in the
        world editor.
        """
        self.client.force_authenticate(self.user)
        self.assertFalse(self.player.is_immortal)
        resp = self.client.put(
            reverse('builder-player-detail', args=[
                self.world.pk, self.player.pk]),
            {
                'id': self.player.id,
                'is_immortal': True
            })
        self.assertEqual(resp.status_code, 200)
        self.player.refresh_from_db()
        self.assertTrue(self.player.is_immortal)

    def test_reset_player_in_other_world_returns_404(self):
        other_user = self.create_user('other@example.com')
        other_world = World.objects.new_world(
            name='Other World',
            author=other_user)
        other_spawn = other_world.create_spawn_world()
        other_player = Player.objects.create(
            name='Rogue',
            room=other_world.rooms.first(),
            user=other_user,
            world=other_spawn)

        class SimplePlayerSerializer(serializers.ModelSerializer):
            class Meta:
                model = Player
                fields = ['id']

        with mock.patch('spawns.models.Player.reset', autospec=True) as reset, \
                mock.patch(
                    'builders.views.PlayerDetailViewSet.serializer_class',
                    SimplePlayerSerializer):
            reset.side_effect = lambda player: player
            resp = self.client.post(
                reverse('builder-player-reset', args=[
                    self.world.pk, other_player.pk]))
        self.assertEqual(resp.status_code, 404)


class ProcessionTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.list_ep = reverse('builder-procession-list',
                               args=[self.world.pk, self.zone.pk])
        self.faction = Faction.objects.create(
            code='faction',
            name='Faction',
            world=self.world,
            is_core=True)

    def test_procession_create(self):
        resp = self.client.post(self.list_ep, {
            'room': self.room.key,
            'faction': self.faction.key,
        })
        self.assertEqual(resp.status_code, 201)
        procession = Procession.objects.get(pk=resp.data['id'])
        self.assertEqual(procession.room, self.room)
        self.assertEqual(procession.faction, self.faction)

        resp = self.client.post(self.list_ep, {})
        self.assertEqual(resp.status_code, 400)

        self.assertTrue(
            self.room.flags.filter(code=adv_consts.ROOM_FLAG_PEACEFUL).exists)

    def test_cannot_create_duplicate_procession(self):
        "Tests for uniqueness of faction / room procession pair"
        procession = Procession.objects.create(
            room=self.room,
            faction=self.faction)
        resp = self.client.post(self.list_ep, {
            'room': self.room.key,
            'faction': self.faction.key,
        })
        self.assertEqual(resp.status_code, 400)

    def test_procession_edit_faction(self):
        procession = Procession.objects.create(
            room=self.room,
            faction=self.faction)

        faction2 = Faction.objects.create(
            code='faction2',
            name='Faction 2',
            world=self.world,
            is_core=True)

        resp = self.client.put(
            reverse('builder-procession-detail', args=[
                self.world.pk, self.zone.pk, procession.pk]),
            {
                'room': self.room.key,
                'faction': faction2.key,
            })
        self.assertEqual(resp.status_code, 200)
        procession.refresh_from_db()
        self.assertEqual(procession.faction, faction2)

    def test_procession_edit_room(self):
        procession = Procession.objects.create(
            room=self.room, faction=self.faction)
        self.room.flags.create(
            room=self.room,
            code=adv_consts.ROOM_FLAG_PEACEFUL)
        room2 = Room.objects.create(
            world=self.world, x=1, y=0, z=0)

        resp = self.client.put(
            reverse('builder-procession-detail', args=[
                self.world.pk, self.zone.pk, procession.pk]),
                {
                    'room': room2.key,
                    'faction': self.faction.key
                })
        self.assertEqual(resp.status_code, 200)
        procession.refresh_from_db()
        self.assertEqual(procession.room, room2)
        self.assertFalse(
            self.room.flags.filter(code=adv_consts.ROOM_FLAG_PEACEFUL))
        self.assertTrue(
            room2.flags.filter(code=adv_consts.ROOM_FLAG_PEACEFUL))


    def test_procession_delete(self):
        procession = Procession.objects.create(
            room=self.room,
            faction=self.faction)
        self.room.flags.create(
            code=adv_consts.ROOM_FLAG_PEACEFUL,
            room=self.room)

        resp = self.client.delete(
            reverse('builder-procession-detail', args=[
                self.world.pk, self.zone.pk, procession.pk]))
        self.assertEqual(resp.status_code, 204)
        with self.assertRaises(Procession.DoesNotExist):
            procession.refresh_from_db()

        self.assertFalse(
            self.room.flags.filter(code=adv_consts.ROOM_FLAG_PEACEFUL))


class CreateFoodTests(BuilderTestCase):

    def test_stamina_default(self):
        """
        Tests that when creating a food item, its default food type is stamina
        """
        endpoint = reverse('builder-item-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'name': 'a ration',
            'type': adv_consts.ITEM_TYPE_FOOD,
        })
        self.assertEqual(resp.status_code, 201)
        template = ItemTemplate.objects.get(pk=resp.data['id'])
        self.assertEqual(template.food_type, 'stamina')

    def test_create_health_food(self):
        endpoint = reverse('builder-item-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'name': 'a ration',
            'type': adv_consts.ITEM_TYPE_FOOD,
            'food_type': adv_consts.ITEM_FOOD_TYPE_HEALTH,
        })
        self.assertEqual(resp.status_code, 201)
        template = ItemTemplate.objects.get(pk=resp.data['id'])
        self.assertEqual(template.food_type, 'health')

    def test_food_item_must_have_food_type(self):
        item_template = ItemTemplate.objects.create(
            name='a ration',
            type=adv_consts.ITEM_TYPE_FOOD,
            food_type=adv_consts.ITEM_FOOD_TYPE_STAMINA,
            world=self.world)
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        resp = self.client.put(endpoint, {'food_type': ''})
        self.assertEqual(resp.status_code, 400)


class FactTests(BuilderTestCase):

    def test_set_existing_fact(self):
        schedule = FactSchedule.objects.create(
            world=self.world,
            name='Tower Control',
            fact='tower_control',
            value='orc secondvaluedoesnotmatter',
            schedule='10')

        result = schedule.run({'tower_control': 'human'})
        self.assertEqual(result['fact'], 'tower_control')
        self.assertEqual(result['old_value'], 'human')
        self.assertEqual(result['new_value'], 'orc')

    def test_set_new_fact(self):
        schedule = FactSchedule.objects.create(
            world=self.world,
            name='Tower Control',
            fact='tower_control',
            value='orc',
            schedule='10')

        result = schedule.run({})
        self.assertEqual(result['fact'], 'tower_control')
        self.assertEqual(result['old_value'], '')
        self.assertEqual(result['new_value'], 'orc')

    def test_schedule_cycle(self):
        schedule = FactSchedule.objects.create(
            world=self.world,
            name='Seasons',
            selection='cycle',
            fact='season',
            value='summer fall winter spring',
            schedule='10')

        result = schedule.run({'season': 'summer'})
        self.assertEqual(result['fact'], 'season')
        self.assertEqual(result['old_value'], 'summer')
        self.assertEqual(result['new_value'], 'fall')

        result = schedule.run({'season': 'fall'})
        self.assertEqual(result['new_value'], 'winter')

        result = schedule.run({'season': 'fall'})
        self.assertEqual(result['new_value'], 'winter')

    def test_change_msg(self):
        schedule = FactSchedule.objects.create(
            world=self.world,
            name='Season',
            fact='season',
            value='spring',
            schedule='10',
            change_msg='{{old_value}} gives way to {{new_value}}.')

        result = schedule.run({'season': 'winter'})
        self.assertEqual(result['msg'], 'Winter gives way to spring.')

        # An invalie change_msg shouldn't break things
        schedule.change_msg = '{{doesnotexist}} something'
        schedule.save()
        self.assertEqual(
            schedule.run({'season': 'winter'})['msg'],
            ' something')

    def test_delete_fact_schedule(self):
        "Regression test that a fact schedule can be deleted"
        schedule = FactSchedule.objects.create(
            world=self.world,
            name='Season',
            fact='season',
            value='spring',
            schedule='10',
            change_msg='{{old_value}} gives way to {{new_value}}.')
        endpoint = reverse('builder-fact-schedule-details',
                           args=[self.world.pk, schedule.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 204)


class WorldReviewTests(BuilderTestCase):

    def test_unsubmitted_world(self):
        self.assertEqual(
            self.world.review_status,
            api_consts.WORLD_REVIEW_STATUS_UNSUBMITTED)

    def test_submit_world_workflow(self):
        self.assertEqual(WorldReview.objects.count(), 0)

        description = 'This is a world that is ready for review.'

        # Submit review
        endpoint = reverse('builder-review-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'description': description
        })
        self.assertEqual(resp.status_code, 201)
        review = WorldReview.objects.get(pk=resp.data['id'])
        self.assertEqual(review.status,
                         api_consts.WORLD_REVIEW_STATUS_SUBMITTED)
        self.assertEqual(review.description, description)
        self.assertEqual(resp.data['status'],
                         api_consts.WORLD_REVIEW_STATUS_SUBMITTED)

        # Submitting the world again gives an error because it's already submitted
        endpoint = reverse('builder-review-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'description': description
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                         'Only one review can be submitted at a time.')

        # A non-staff member trying to claim the review fails
        self.assertFalse(self.user.is_staff)
        endpoint = reverse('builder-review-detail-claim',
                           args=[self.world.pk, review.pk])
        resp = self.client.post(endpoint, {})
        self.assertEqual(resp.status_code, 403)

        # A staff member claims the review
        staff = self.create_user('staff@writtenrealms.com', is_staff=True)
        self.client.force_authenticate(staff)
        endpoint = reverse('builder-review-detail-claim',
                           args=[self.world.pk, review.pk])
        resp = self.client.post(endpoint, {})
        self.assertEqual(resp.status_code, 201)
        review.refresh_from_db()
        self.assertEqual(review.reviewer, staff)

        # A different staff member claims the review
        staff2 = self.create_user('staff2@writtenrealms.com', is_staff=True)
        self.client.force_authenticate(staff2)
        endpoint = reverse('builder-review-detail-claim',
                           args=[self.world.pk, review.pk])
        resp = self.client.post(endpoint, {})
        self.assertEqual(resp.status_code, 201)
        review.refresh_from_db()
        self.assertEqual(review.reviewer, staff2)

        # Staff member can now either approve or reject the review.
        # If they reject, a review must be provided.
        endpoint = reverse('builder-review-detail-resolve',
                           args=[self.world.pk, review.pk])
        resp = self.client.post(endpoint, {
            'status': api_consts.WORLD_REVIEW_STATUS_REVIEWED})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                         "A review must have a text field if it's not "
                         "approved.")
        resp = self.client.post(endpoint,
                                {'status': api_consts.WORLD_REVIEW_STATUS_REVIEWED,
                                 'text': 'This is a review.'})
        self.assertEqual(resp.status_code, 201)
        review.refresh_from_db()
        self.assertEqual(review.status, api_consts.WORLD_REVIEW_STATUS_REVIEWED)
        self.assertEqual(review.text, 'This is a review.')
        # If they approve, a review is optional.
        review.text = None
        review.status = api_consts.WORLD_REVIEW_STATUS_SUBMITTED
        review.save()
        resp = self.client.post(endpoint,
                                {'status': api_consts.WORLD_REVIEW_STATUS_APPROVED})
        self.assertEqual(resp.status_code, 201)
        review.refresh_from_db()
        self.assertEqual(review.status, api_consts.WORLD_REVIEW_STATUS_APPROVED)

        # Once a review has been resolved, it can't be claimed again, nor can it be resolved a
        # second time.
        endpoint = reverse('builder-review-detail-claim',
                           args=[self.world.pk, review.pk])
        resp = self.client.post(endpoint, {})
        self.assertEqual(resp.status_code, 400)
        endpoint = reverse('builder-review-detail-resolve',
                           args=[self.world.pk, review.pk])
        resp = self.client.post(endpoint, {
            'status': api_consts.WORLD_REVIEW_STATUS_REVIEWED,
            'text': ''})
        self.assertEqual(resp.status_code, 400)

    def test_description_mandatory(self):
        # A world review submission is just a ping. No data passed in.
        endpoint = reverse('builder-review-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {})

    def test_cannot_resubmit_before_delay(self):
        # Create review that was just rejected
        reviewer = self.create_user('staff@writtenrealms.com', is_staff=True)
        WorldReview.objects.create(
                status=api_consts.WORLD_REVIEW_STATUS_REVIEWED,
                world=self.world,
                reviewer=reviewer)

        endpoint = reverse('builder-review-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'description': 'Review description'
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data[0],
                         "Cannot resubmit for another 30 days.")

class CustomSkillDefinitionTests(BuilderTestCase):

    def test_create_custom_skill(self):
        endpoint = reverse('builder-skill-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {'code': 'myskill'})
        self.assertEqual(resp.status_code, 201)

    def test_code_validation(self):
        endpoint = reverse('builder-skill-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {'code': 'my skill'})
        self.assertEqual(resp.status_code, 400)


class BuilderAssignmentTests(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.builder = self.world.add_builder(self.user)

    def test_create_builder_assignment(self):
        endpoint = reverse('builder-assignment-list',
                           args=[self.world.pk, self.builder.pk])
        resp = self.client.post(endpoint, data={
            'assignment': self.zone.key,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(BuilderAssignment.objects.count(), 1)
        assignment = BuilderAssignment.objects.get(pk=resp.data['id'])
        self.assertEqual(assignment.builder, self.builder)
        self.assertEqual(assignment.assignment, self.zone)

    def test_get_builder_assignments(self):
        endpoint = reverse('builder-assignment-list',
                           args=[self.world.pk, self.builder.pk])
        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'], [])

        assignment = BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.zone)

        jack = User.objects.create_user('jack@example.com', 'p')
        builder2 = self.builder = self.world.add_builder(jack, read_only=False)
        assignment2 = BuilderAssignment.objects.create(
            builder=builder2,
            assignment=self.zone)

        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['id'], assignment.pk)

    def test_delete_builder_assignment(self):
        assignment = BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.zone)
        endpoint = reverse('builder-assignment-details',
                           args=[self.world.pk,
                                 self.builder.pk,
                                 assignment.pk])
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(BuilderAssignment.objects.count(), 0)


class BuilderPermissionsBase(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.builder_user = User.objects.create_user(
            'builder_user@example.com', 'p')
        self.builder = self.world.add_builder(self.builder_user)
        self.client.force_authenticate(self.builder_user)


class BuilderMobTemplatePermissionTests(BuilderPermissionsBase):

    def test_view_mob_template_list_no_builder(self):
        """
        Non-builders should not be able to view builder endpoints. Since this
        is achieved via the IsWorldBuilder permission, we assume that it works
        for everything.
        """
        not_builder = User.objects.create_user('not_builder@example.com', 'p')
        self.client.force_authenticate(not_builder)
        resp = self.client.get(reverse('builder-mob-template-list',
                                       args=[self.world.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_view_mob_template_details(self):
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a soldier')
        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])

        # Rank 2 and above will be able to see it
        self.builder.builder_rank = 2
        self.builder.save()
        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 200)

        # Rank 1 would need to be explicitly assigned that mob
        self.builder.builder_rank = 1
        self.builder.save()
        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=mob_template)
        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 200)

    def test_edit_mob_template(self):
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a soldier')
        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])

        # Rank 3 and above will be able to edit any template
        self.builder.builder_rank = 3
        self.builder.save()
        resp = self.client.put(endpoint, {'name': 'a warrior'}, format='json')
        self.assertEqual(resp.status_code, 200)

        # Rank 2 and below would need to be explicitly assigned the mob
        self.builder.builder_rank = 2
        self.builder.save()
        resp = self.client.put(endpoint, {'name': 'a mage'}, format='json')
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=mob_template)
        resp = self.client.put(endpoint, {'name': 'a mage'}, format='json')
        self.assertEqual(resp.status_code, 200)

        self.builder.builder_rank = 1
        self.builder.save()
        resp = self.client.put(endpoint, {'name': 'a cleric'}, format='json')
        self.assertEqual(resp.status_code, 200)

        BuilderAssignment.objects.all().delete()
        resp = self.client.put(endpoint, {'name': 'an assassin'}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_create_mob_template(self):
        # Rank 3 and above can create mob templates and it won't
        # create an assignment.
        self.builder.builder_rank = 3
        self.builder.save()
        endpoint = reverse('builder-mob-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'name': 'a soldier',
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(MobTemplate.objects.count(), 1)
        self.assertEqual(BuilderAssignment.objects.count(), 0)
        return

        # If a rank 1 or 2 create a mob however, a assignment will be
        # implicitly created.
        self.builder.builder_rank = 2
        self.builder.save()
        resp = self.client.post(endpoint, {
            'name': 'a priest',
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(MobTemplate.objects.count(), 2)
        self.assertEqual(BuilderAssignment.objects.count(), 1)
        assignment = BuilderAssignment.objects.get()
        self.assertEqual(assignment.builder, self.builder)

    def test_delete_mob_template(self):
        # Rank 3 and above can delete any mob template
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a soldier')
        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])
        self.builder.builder_rank = 3
        self.builder.save()
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(MobTemplate.objects.count(), 0)

        # Rank 2 and below require an assignment
        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a soldier')
        endpoint = reverse('builder-mob-template-detail',
                           args=[self.world.pk, mob_template.pk])
        self.builder.builder_rank = 2
        self.builder.save()
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=mob_template)
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 204)


class BuilderItemTemplatePermissionTests(BuilderPermissionsBase):

    def test_view_item_template_details(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a sword')
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])

        # Rank 2 and above will be able to see it
        self.builder.builder_rank = 2
        self.builder.save()
        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 200)

        # Rank 1 would need to be explicitly assigned that item
        self.builder.builder_rank = 1
        self.builder.save()
        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=item_template)
        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 200)

    def test_edit_item_template(self):
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a sword')
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])

        # Rank 3 and above will be able to edit any template
        self.builder.builder_rank = 3
        self.builder.save()
        resp = self.client.put(endpoint, {'name': 'a dagger'}, format='json')
        self.assertEqual(resp.status_code, 200)

        # Rank 2 and below would need to be explicitly assigned the item
        self.builder.builder_rank = 2
        self.builder.save()
        resp = self.client.put(endpoint, {'name': 'a staff'}, format='json')
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=item_template)
        resp = self.client.put(endpoint, {'name': 'a staff'}, format='json')
        self.assertEqual(resp.status_code, 200)

        self.builder.builder_rank = 1
        self.builder.save()
        resp = self.client.put(endpoint, {'name': 'a wand'}, format='json')
        self.assertEqual(resp.status_code, 200)

        BuilderAssignment.objects.all().delete()
        resp = self.client.put(endpoint, {'name': 'a rod'}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_create_item_template(self):

        # Rank 3 and above can create item templates and it won't
        # create an assignment.
        self.builder.builder_rank = 3
        self.builder.save()
        endpoint = reverse('builder-item-template-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'name': 'a sword',
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(ItemTemplate.objects.count(), 1)
        self.assertEqual(BuilderAssignment.objects.count(), 0)

        # If a rank 1 or 2 create an item however, a assignment will be
        # implicitly created.
        self.builder.builder_rank = 2
        self.builder.save()
        resp = self.client.post(endpoint, {
            'name': 'a staff',
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(ItemTemplate.objects.count(), 2)
        self.assertEqual(BuilderAssignment.objects.count(), 1)
        assignment = BuilderAssignment.objects.get()
        self.assertEqual(assignment.builder, self.builder)

    def test_delete_item_template(self):
        # Rank 3 and above can delete any item template
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a sword')
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        self.builder.builder_rank = 3
        self.builder.save()
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(ItemTemplate.objects.count(), 0)

        # Rank 2 and below require an assignment
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a sword')
        endpoint = reverse('builder-item-template-detail',
                           args=[self.world.pk, item_template.pk])
        self.builder.builder_rank = 2
        self.builder.save()
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=item_template)
        resp = self.client.delete(endpoint)
        self.assertEqual(resp.status_code, 204)


class BuilderLoaderPermissionTests(BuilderPermissionsBase):

    def test_rank_2_permissions(self):
        self.builder.builder_rank = 2
        self.builder.save()

        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            name='Loader A')
        loader_endpoint = reverse('builder-loader-detail',
                                  args=[self.world.pk, loader.pk])
        loader_rules_endpoint = reverse('builder-loader-rule-list',
                                        args=[self.world.pk, loader.pk])
        template = ItemTemplate.objects.create(
            world=self.world, name='an item')
        rule = Rule.objects.create(
            loader=loader,
            template=template,
            target=self.room)
        loader_rule_endpoint = reverse(
            'builder-loader-rule-detail',
            args=[self.world.pk, loader.pk, rule.pk])

        # Rank 2 builders can see loaders and rules
        resp = self.client.get(loader_endpoint)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(loader_rules_endpoint)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(loader_rule_endpoint)
        self.assertEqual(resp.status_code, 200)

        # But cannot edit them unless they have an assignment
        resp = self.client.put(loader_endpoint, {
            'name': 'Loader B'
        }, format='json')
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post(loader_rules_endpoint, {
            'template': template.key,
            'target': self.room.key,
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.put(loader_rule_endpoint, {
            'template': template.key,
            'target': self.room.key,
            'qty': 2,
        })
        self.assertEqual(resp.status_code, 403)

        # If they have an assignment, they can edit
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.zone)
        resp = self.client.put(loader_endpoint, {
            'name': 'Loader B'
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(loader_rules_endpoint, {
            'template': template.key,
            'target': self.room.key,
        })
        self.assertEqual(resp.status_code, 201)
        resp = self.client.put(loader_rule_endpoint, {
            'template': template.key,
            'target': self.room.key,
            'qty': 2,
        })
        self.assertEqual(resp.status_code, 200)

    def test_rank_1_permissions(self):
        """
        Same as for rank 2 except that they also can't read without an
        assignment.
        """
        self.builder.builder_rank = 1
        self.builder.save()

        loader = Loader.objects.create(
            world=self.world,
            zone=self.zone,
            name='Loader A')
        loader_endpoint = reverse('builder-loader-detail',
                                  args=[self.world.pk, loader.pk])
        loader_rules_endpoint = reverse('builder-loader-rule-list',
                                        args=[self.world.pk, loader.pk])
        template = ItemTemplate.objects.create(
            world=self.world, name='an item')
        rule = Rule.objects.create(
            loader=loader,
            template=template,
            target=self.room)
        loader_rule_endpoint = reverse(
            'builder-loader-rule-detail',
            args=[self.world.pk, loader.pk, rule.pk])

        # Rank 1 builders can't see loaders or rules
        resp = self.client.get(loader_endpoint)
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get(loader_rules_endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'], [])
        resp = self.client.get(loader_rule_endpoint)
        self.assertEqual(resp.status_code, 404)

        # But once they have a zone assignment they can
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.zone)
        resp = self.client.get(loader_endpoint)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(loader_rules_endpoint)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        resp = self.client.get(loader_rule_endpoint)
        self.assertEqual(resp.status_code, 200)


class BuilderQuestPermissionTests(BuilderPermissionsBase):

    def test_rank_2_permissions(self):
        self.builder.builder_rank = 2
        self.builder.save()

        template = MobTemplate.objects.create(
            world=self.world, name='a mob')

        quest = Quest.objects.create(
            world=self.world,
            zone=self.zone,
            mob_template=template,
            name='Quest A')
        quest_endpoint = reverse('builder-quest-detail',
                                 args=[self.world.pk, quest.pk])
        quest_objectives_endpoint = reverse('builder-objective-list',
                                            args=[self.world.pk, quest.pk])
        quest_rewards_endpoint = reverse('builder-reward-list',
                                         args=[self.world.pk, quest.pk])
        objective = Objective.objects.create(
            quest=quest,
            type=adv_consts.OBJECTIVE_TYPE_GOLD,
            qty=100)
        objective_endpoint = reverse('builder-objective-detail',
                                     args=[self.world.pk, objective.pk])
        reward = Reward.objects.create(
            type=adv_consts.REWARD_TYPE_EXP,
            quest=quest,
            qty=1000)
        reward_endpoint = reverse('builder-reward-detail',
                                  args=[self.world.pk, reward.pk])

        # Rank 2 builders can see quests, objectives and rewards
        resp = self.client.get(quest_endpoint)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(objective_endpoint)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reward_endpoint)
        self.assertEqual(resp.status_code, 200)

        # But cannot edit them unless they have an assignment
        resp = self.client.put(quest_endpoint, {
            'name': 'Quest B'
        }, format='json')
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post(quest_objectives_endpoint, {
            'type': adv_consts.OBJECTIVE_TYPE_GOLD,
            'qty': 100,
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.put(objective_endpoint, {
            'type': adv_consts.OBJECTIVE_TYPE_GOLD,
            'qty': 200,
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post(quest_rewards_endpoint, {
            'type': adv_consts.REWARD_TYPE_GOLD,
            'qty': 100,
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.put(reward_endpoint, {
            'type': adv_consts.REWARD_TYPE_EXP,
            'qty': 500,
        })
        self.assertEqual(resp.status_code, 403)

        # If they have an assignment, they can edit
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.zone)
        resp = self.client.put(quest_endpoint, {
            'name': 'Quest B'
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(quest_objectives_endpoint, {
            'type': adv_consts.OBJECTIVE_TYPE_GOLD,
            'qty': 100,
        })
        self.assertEqual(resp.status_code, 201)
        resp = self.client.put(objective_endpoint, {
            'type': adv_consts.OBJECTIVE_TYPE_GOLD,
            'qty': 200,
        })
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(quest_rewards_endpoint, {
            'type': adv_consts.REWARD_TYPE_GOLD,
            'qty': 100,
        })
        self.assertEqual(resp.status_code, 201)
        resp = self.client.put(reward_endpoint, {
            'type': adv_consts.REWARD_TYPE_EXP,
            'qty': 500,
        })
        self.assertEqual(resp.status_code, 200)

    def test_rank_2_permissions(self):
        """
        Same as for rank 2 except that they also can't read without an
        assignment.
        """
        self.builder.builder_rank = 1
        self.builder.save()

        template = MobTemplate.objects.create(
            world=self.world, name='a mob')

        quest = Quest.objects.create(
            world=self.world,
            zone=self.zone,
            mob_template=template,
            name='Quest A')
        quest_endpoint = reverse('builder-quest-detail',
                                 args=[self.world.pk, quest.pk])
        objective = Objective.objects.create(
            quest=quest,
            type=adv_consts.OBJECTIVE_TYPE_GOLD,
            qty=100)
        reward = Reward.objects.create(
            type=adv_consts.REWARD_TYPE_EXP,
            quest=quest,
            qty=1000)

        # Rank 1 builders can't see quests, objectives or rewards
        resp = self.client.get(quest_endpoint)
        self.assertEqual(resp.status_code, 403)

        # But once they have a zone assignment they can
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.zone)
        resp = self.client.get(quest_endpoint)
        self.assertEqual(resp.status_code, 200)


class BuilderRoomPermissionTests(BuilderPermissionsBase):

    def test_rank_2_permissions(self):
        self.builder.builder_rank = 2
        self.builder.save()
        endpoint = reverse('builder-room-detail',
                           args=[self.world.pk, self.room.pk])

        # Rank 2 builder can see all rooms
        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 200)

        # But can only edit them with a builder assignment
        resp = self.client.put(endpoint, {'name': 'New Room'}, format='json')
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.room)

        resp = self.client.put(endpoint, {'name': 'New Room'}, format='json')
        self.assertEqual(resp.status_code, 200)

    def test_rank_1_permissions(self):
        self.builder.builder_rank = 1
        self.builder.save()
        endpoint = reverse('builder-room-detail',
                            args=[self.world.pk, self.room.pk])

        # Cannot see room without a builder assignment
        resp = self.client.get(endpoint)
        self.assertEqual(resp.status_code, 403)

        # But can with one
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.room)
        resp = self.client.get(endpoint)

    def test_rank_2_room_dir_actions(self):
        self.builder.builder_rank = 2
        self.builder.save()

        endpoint = reverse('builder-room-action',
                           args=[self.world.pk, self.room.pk])
        resp = self.client.post(endpoint, {
            'direction': 'north',
            'action': adv_consts.EXIT_ACTION_CREATE
        })
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.room)

        resp = self.client.post(endpoint, {
            'direction': 'north',
            'action': adv_consts.EXIT_ACTION_CREATE
        })
        self.assertEqual(resp.status_code, 201)

    def test_rank_2_room_checks(self):
        self.builder.builder_rank = 2
        self.builder.save()

        check_list_endpoint = reverse('builder-room-checks',
                                      args=[self.world.pk, self.room.pk])
        check = RoomCheck.objects.create(
            room=self.room,
            prevent='enter',
            conditions='level 1')
        check_details_endpoint = reverse('builder-room-check-detail',
                                        args=[self.world.pk, self.room.pk, check.pk])

        resp = self.client.post(check_list_endpoint, {
            'prevent': 'exit',
            'conditions': 'level 2'
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.put(check_details_endpoint, {
            'prevent': 'enter',
            'conditions': 'not level 1',
        })
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.room)

        resp = self.client.post(check_list_endpoint, {
            'prevent': 'exit',
            'conditions': 'level 2'
        })
        self.assertEqual(resp.status_code, 201)
        resp = self.client.put(check_details_endpoint, {
            'prevent': 'enter',
            'conditions': 'not level 1',
        })
        self.assertEqual(resp.status_code, 200)

    def test_rank_2_room_actions(self):
        self.builder.builder_rank = 2
        self.builder.save()

        action_list_endpoint = reverse('builder-room-action-list',
                                       args=[self.world.pk, self.room.pk])
        action = RoomAction.objects.create(
            room=self.room,
            actions='trigger',
            commands='echo something happens')
        action_details_endpoint = reverse('builder-room-action-detail',
                                         args=[self.world.pk, self.room.pk, action.pk])

        resp = self.client.post(action_list_endpoint, {
            'actions': 'trigger',
            'commands': 'echo something happens'
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.put(action_details_endpoint, {
            'actions': 'trigger',
            'commands': 'echo something happens'
        })
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.room)

        resp = self.client.post(action_list_endpoint, {
            'actions': 'trigger',
            'commands': 'echo something happens'
        })
        self.assertEqual(resp.status_code, 201)
        resp = self.client.put(action_details_endpoint, {
            'actions': 'trigger',
            'commands': 'echo something happens'
        })
        self.assertEqual(resp.status_code, 200)

    def test_rank_2_room_details(self):
        # builder-room-detail-list
        # builder-room-detail-detail
        self.builder.builder_rank = 2
        self.builder.save()

        detail_list_endpoint = reverse('builder-room-detail-list',
                                args=[self.world.pk, self.room.pk])
        detail = RoomDetail.objects.create(
            room=self.room,
            keywords='bookshelf',
            description='A dusty bookshelf.')
        detail_details_endpoint = reverse(
            'builder-room-detail-detail',
            args=[self.world.pk, self.room.pk, detail.pk])

        resp = self.client.post(detail_list_endpoint, {
            'keywords': 'book',
            'description': 'A thin book.'
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.put(detail_details_endpoint, {
            'keywords': 'bookshelf',
            'description': 'A pristine bookshelf.'
        })
        self.assertEqual(resp.status_code, 403)

        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.room)

        resp = self.client.post(detail_list_endpoint, {
            'keywords': 'book',
            'description': 'A thin book.'
        })
        self.assertEqual(resp.status_code, 201)
        resp = self.client.put(detail_details_endpoint, {
            'keywords': 'bookshelf',
            'description': 'A pristine bookshelf.'
        })
        self.assertEqual(resp.status_code, 200)

    def test_rank_2_loads_in(self):
        self.builder.builder_rank = 2
        self.builder.save()

        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a sword')
        endpoint = reverse('builder-room-loads',
                           args=[self.world.pk, self.room.pk])

        # A rank 2 builder can't add this room load without an assignment
        resp = self.client.post(endpoint, {
            'template': item_template.key,
        })
        self.assertEqual(resp.status_code, 403)

        # With an assignment, it works
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=self.room)
        resp = self.client.post(endpoint, {
            'template': item_template.key,
        })
        self.assertEqual(resp.status_code, 201)

    def test_rank_2_merchant_inventory(self):
        self.builder.builder_rank = 2
        self.builder.save()

        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a merchant')
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')

        endpoint = reverse('builder-mob-template-merchant-inventory-list',
                           args=[self.world.pk, mob_template.pk])

        # A rank 2 builder can't add merchant inventory without an assignment
        resp = self.client.post(endpoint, {
            'item_template': item_template.key,
            'num': 2,
        })
        self.assertEqual(resp.status_code, 403)

        # With an assignment, it works
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=mob_template)
        resp = self.client.post(endpoint, {
            'item_template': item_template.key,
            'num': 2,
        })
        self.assertEqual(resp.status_code, 201)

    def test_rank_2_mob_inventory(self):
        self.builder.builder_rank = 2
        self.builder.save()

        mob_template = MobTemplate.objects.create(
            world=self.world,
            name='a mob')
        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')

        # A rank 2 builder can't add mob inventory without an assignment
        endpoint = reverse('builder-mob-template-inventory',
                           args=[self.world.pk, mob_template.pk])
        resp = self.client.post(endpoint, {
            'item_template': item_template.key,
        })
        self.assertEqual(resp.status_code, 403)

        # With an assignment, it works
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=mob_template)
        resp = self.client.post(endpoint, {
            'item_template': item_template.key,
        })
        self.assertEqual(resp.status_code, 201)

    def test_rank_2_item_inventory(self):
        self.builder.builder_rank = 2
        self.builder.save()

        bag_template = ItemTemplate.objects.create(
            world=self.world,
            name='a bag',
            type='container')
        rock_template = ItemTemplate.objects.create(
            world=self.world,
            name='a rock')

        # A rank 2 builder can't add item inventory without an assignment
        endpoint = reverse('builder-item-template-inventory',
                           args=[self.world.pk, bag_template.pk])
        resp = self.client.post(endpoint, {
            'item_template': rock_template.key,
        })
        self.assertEqual(resp.status_code, 403)

        # With an assignment, it works
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=bag_template)
        resp = self.client.post(endpoint, {
            'item_template': rock_template.key,
        })
        self.assertEqual(resp.status_code, 201)

    def test_rank_2_item_actions(self):
        self.builder.builder_rank = 2
        self.builder.save()

        item_template = ItemTemplate.objects.create(
            world=self.world,
            name='a bench')
        action = ItemAction.objects.create(
            item_template=item_template,
            actions='sit',
            commands='echo You sit on the bench.')
        list_endpoint = reverse('builder-item-action-list',
                                args=[self.world.pk, item_template.pk])
        detail_endpoint = reverse('builder-item-action-detail',
                                  args=[self.world.pk, item_template.pk, action.pk])

        # A rank 2 builder can't add item actions without an assignment
        resp = self.client.post(list_endpoint, {
            'actions': 'lay',
            'commands': 'echo You lay on the bench.'
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.put(detail_endpoint, {
            'actions': 'sit',
            'commands': 'echo You slowly sit on the bench.'
        })

        # With an assignment, it works
        BuilderAssignment.objects.create(
            builder=self.builder,
            assignment=item_template)
        resp = self.client.post(list_endpoint, {
            'actions': 'lay',
            'commands': 'echo You lay on the bench.'
        })
        self.assertEqual(resp.status_code, 201)
        resp = self.client.put(detail_endpoint, {
            'actions': 'sit',
            'commands': 'echo You slowly sit on the bench.'
        })
        self.assertEqual(resp.status_code, 200)


class BuildersCreatingBuildersTests(BuilderPermissionsBase):

    def test_rank_2_builders_cannot_create_builders(self):
        "Test that rank 2 builders cannot create builders."
        self.builder.builder_rank = 2
        self.builder.save()
        endpoint = reverse('builder-builder-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'user': self.user.key,
            'builder_rank': 1,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(WorldBuilder.objects.count(), 1)

    def test_rank_3_can_create_rank_2_builders(self):
        "Test that rank 3 builders can create rank 1 or 2 builders."
        self.builder.builder_rank = 3
        self.builder.save()
        endpoint = reverse('builder-builder-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'user': self.user.key,
            'builder_rank': 2,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(WorldBuilder.objects.count(), 2)

    def test_rank_3_can_only_make_builders_up_to_rank_2(self):
        "Test that rank 3 builders can only create rank 1 or 2 builders."
        self.builder.builder_rank = 3
        self.builder.save()
        endpoint = reverse('builder-builder-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'user': self.user.key,
            'builder_rank': 3,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(WorldBuilder.objects.count(), 1)

    def test_rank_4_can_make_other_rank_4s(self):
        "Test that rank 4 builders can create other rank 4 builders."
        self.builder.builder_rank = 4
        self.builder.save()
        endpoint = reverse('builder-builder-list', args=[self.world.pk])
        resp = self.client.post(endpoint, {
            'user': self.user.key,
            'builder_rank': 4,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(WorldBuilder.objects.count(), 2)

    def test_rank_3_can_only_edit_up_to_rank_3(self):
        "Test that rank 3 builders can only edit builders up to rank 2."
        self.builder.builder_rank = 3
        self.builder.save()
        builder2 = self.world.add_builder(self.user, builder_rank=4)
        endpoint = reverse('builder-builder-detail', args=[self.world.pk, builder2.pk])
        resp = self.client.put(endpoint, {'builder_rank': 3})
        self.assertEqual(resp.status_code, 400)

        self.builder.builder_rank = 4
        self.builder.save()
        resp = self.client.put(endpoint, {'builder_rank': 4})
        self.assertEqual(resp.status_code, 200)
        builder2.refresh_from_db()
        self.assertEqual(builder2.builder_rank, 4)
