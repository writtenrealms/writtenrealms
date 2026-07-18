import mock
import collections

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from rest_framework import serializers
from rest_framework.reverse import reverse

from config import constants as adv_consts

from core.scoped_state import STATE_SCOPE_WORLD, replace_state_snapshot

from config import constants as api_consts
from builders.models import (
    BuilderAssignment,
    Currency,
    ItemDefinition,
    Path,
    PathRoom,
    Procession,
    Faction,
    FactionAssignment,
    FactionRank,
    FactSchedule,
    RoomAction,
    Trigger,
    WorldBuilder,
    WorldReview)
from builders import serializers as builder_serializers
from tests.base import WorldTestCase
from spawns import serializers as spawn_serializers
from spawns.models import Player, Mob, DoorState, Item
from users.models import User
from worlds.models import (
    InstanceParticipant,
    InstanceRun,
    World,
    Zone,
    Room,
    RoomFlag,
    RoomDetail,
    Door,
)


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
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.combat_system = {
            'profiles': {
                'basic_physical': {
                    'power_scale': 1.25,
                },
            },
        }
        self.world.config.death_mode = adv_consts.DEATH_MODE_DESTROY_EQ
        self.world.config.death_gold_penalty = 0.35
        self.world.config.ability_progression = {
            'max_known': 'uncapped',
            'starting_abilities': [{'ability': 'bash'}],
        }
        self.world.config.save(update_fields=[
            'combat_resolution_interval',
            'combat_system',
            'death_mode',
            'death_gold_penalty',
            'ability_progression',
        ])

        resp = self.client.post(self.endpoint, {
            'name': 'New World Instance',
            'instance_of': self.world.pk,
        })
        self.assertEqual(resp.status_code, 201)
        instance = World.objects.get(pk=resp.json()['id'])
        self.assertEqual(instance.name, 'New World Instance')
        self.assertEqual(instance.instance_of, self.world)
        self.assertTrue(instance.is_multiplayer)
        self.assertNotEqual(instance.config_id, self.world.config_id)
        self.assertNotEqual(instance.config.combat_resolution_interval, 1.5)
        self.assertNotEqual(instance.config.combat_system, self.world.config.combat_system)
        self.assertEqual(instance.config.death_mode, adv_consts.DEATH_MODE_DESTROY_EQ)
        self.assertEqual(instance.config.death_gold_penalty, 0.35)
        self.assertNotEqual(
            instance.config.ability_progression,
            self.world.config.ability_progression)
        self.assertEqual(instance.config.starting_room.world, instance)
        self.assertEqual(instance.config.death_room.world, instance)
        self.assertNotEqual(
            instance.config.starting_room_id,
            self.world.config.starting_room_id)

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


class TestWorldAdminInstanceEndpoint(BuilderTestCase):

    def setUp(self):
        super().setUp()
        self.endpoint = reverse(
            'builder-world-admin-instance',
            args=[self.world.pk, self.spawn_world.pk],
        )
        self.reset_endpoint = reverse(
            'builder-world-admin-instance-reset',
            args=[self.world.pk, self.spawn_world.pk],
        )

    def test_returns_wr2_spawn_dashboard_metrics(self):
        now = timezone.now()

        replace_state_snapshot(
            STATE_SCOPE_WORLD,
            self.world,
            {'weather': 'template-sunny'},
        )
        replace_state_snapshot(
            STATE_SCOPE_WORLD,
            self.spawn_world,
            {'weather': 'spawn-rainy', 'lodging_base_price': 12},
        )

        self.player.world = self.spawn_world
        self.player.room = self.room
        self.player.in_game = True
        self.player.last_connection_ts = now
        self.player.last_action_ts = now
        self.player.save(update_fields=[
            'world',
            'room',
            'in_game',
            'last_connection_ts',
            'last_action_ts',
        ])

        offline_player = self.create_player(
            'Offline Joe',
            world=self.spawn_world,
            room=self.room,
        )
        offline_player.in_game = False
        offline_player.save(update_fields=['in_game'])

        Mob.objects.create(
            name='Live Wolf',
            world=self.spawn_world,
            room=self.room,
        )
        Mob.objects.create(
            name='Pending Wolf',
            world=self.spawn_world,
            room=self.room,
            is_pending_deletion=True,
        )

        ground_item = Item.objects.create(
            name='Ground Rock',
            world=self.spawn_world,
            container=self.room,
        )
        chest = Item.objects.create(
            name='Travel Chest',
            type=adv_consts.ITEM_TYPE_CONTAINER,
            world=self.spawn_world,
            container=self.room,
        )
        Item.objects.create(
            name='Nested Gem',
            world=self.spawn_world,
            container=chest,
        )
        Item.objects.create(
            name='Lantern',
            world=self.spawn_world,
            container=self.player,
        )
        Item.objects.create(
            name='Pending Dust',
            world=self.spawn_world,
            container=ground_item,
            is_pending_deletion=True,
        )

        self.spawn_world.last_spawn_plan_run_ts = now
        self.spawn_world.last_extraction_ts = now
        self.spawn_world.last_entered_ts = now
        self.spawn_world.last_played_ts = now
        self.spawn_world.save(update_fields=[
            'last_spawn_plan_run_ts',
            'last_extraction_ts',
            'last_entered_ts',
            'last_played_ts',
        ])

        resp = self.client.get(self.endpoint)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['id'], self.spawn_world.id)
        self.assertEqual(resp.data['lifecycle_details']['current'], self.spawn_world.lifecycle)
        self.assertEqual(resp.data['counts']['mobs_loaded'], 1)
        self.assertEqual(resp.data['counts']['mobs_pending_deletion'], 1)
        self.assertEqual(resp.data['counts']['items_total'], 4)
        self.assertEqual(resp.data['counts']['items_on_ground'], 2)
        self.assertEqual(resp.data['counts']['items_pending_deletion'], 1)
        self.assertEqual(resp.data['counts']['players_logged_in'], 1)
        self.assertEqual(resp.data['counts']['player_records'], 2)
        self.assertEqual(resp.data['counts']['items_by_container_type']['rooms'], 2)
        self.assertEqual(resp.data['counts']['items_by_container_type']['players'], 1)
        self.assertEqual(resp.data['counts']['items_by_container_type']['inside_items'], 1)
        self.assertEqual(resp.data['counts']['items_by_container_type']['without_container'], 0)
        self.assertEqual(len(resp.data['active_players']), 1)
        self.assertEqual(resp.data['active_players'][0]['name'], self.player.name)
        self.assertEqual(resp.data['active_players'][0]['room']['name'], self.room.name)
        self.assertEqual(
            resp.data['world_state'],
            {'weather': 'spawn-rainy', 'lodging_base_price': 12},
        )

    def test_world_admin_lists_spawned_instance_runs(self):
        instance_template = World.objects.new_world(
            name='Battlefield',
            author=self.user,
            is_multiplayer=True,
            instance_of=self.world,
        )
        spawned_instance = instance_template.create_spawn_world(
            instance_ref='battlefield-1',
            leader=self.player,
        )
        now = timezone.now()
        run = InstanceRun.objects.create(
            base_world=self.world,
            template_world=instance_template,
            spawned_world=spawned_instance,
            ref='battlefield-1',
            leader=self.player,
            status=InstanceRun.STATUS_ACTIVE,
            started_at=now,
            last_active_at=now,
            seed='battlefield-1',
        )
        InstanceParticipant.objects.create(
            run=run,
            player=self.player,
            role=InstanceParticipant.ROLE_LEADER,
            transfer_from=self.room,
        )

        resp = self.client.get(reverse('builder-world-admin', args=[self.world.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [spawn_world['id'] for spawn_world in resp.data['spawned_worlds']],
            [self.spawn_world.id],
        )
        self.assertEqual(len(resp.data['instance_runs']), 1)
        payload = resp.data['instance_runs'][0]
        self.assertEqual(payload['id'], run.id)
        self.assertEqual(payload['ref'], 'battlefield-1')
        self.assertEqual(payload['status'], InstanceRun.STATUS_ACTIVE)
        self.assertTrue(payload['is_active'])
        self.assertEqual(payload['template_world']['id'], instance_template.id)
        self.assertEqual(payload['template_world']['name'], 'Battlefield')
        self.assertEqual(payload['spawned_world']['id'], spawned_instance.id)
        self.assertEqual(payload['spawned_world']['lifecycle'], spawned_instance.lifecycle)
        self.assertEqual(payload['leader']['name'], self.player.name)
        self.assertEqual(payload['participant_count'], 1)
        self.assertEqual(payload['active_participant_count'], 1)

    def test_returns_spawned_instance_dashboard_metrics(self):
        instance_template = World.objects.new_world(
            name='Battlefield',
            author=self.user,
            is_multiplayer=True,
            instance_of=self.world,
        )
        spawned_instance = instance_template.create_spawn_world(
            instance_ref='battlefield-1',
            leader=self.player,
        )
        now = timezone.now()
        run = InstanceRun.objects.create(
            base_world=self.world,
            template_world=instance_template,
            spawned_world=spawned_instance,
            ref='battlefield-1',
            leader=self.player,
            status=InstanceRun.STATUS_ACTIVE,
            started_at=now,
            last_active_at=now,
            seed='battlefield-1',
        )
        InstanceParticipant.objects.create(
            run=run,
            player=self.player,
            role=InstanceParticipant.ROLE_LEADER,
            transfer_from=self.room,
        )

        resp = self.client.get(
            reverse(
                'builder-world-admin-instance',
                args=[self.world.pk, spawned_instance.pk],
            )
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['id'], spawned_instance.id)
        self.assertEqual(resp.data['context_world']['id'], instance_template.id)
        self.assertEqual(resp.data['parent_world']['id'], self.world.id)
        self.assertEqual(resp.data['instance_run']['id'], run.id)
        self.assertEqual(resp.data['instance_run']['ref'], 'battlefield-1')
        self.assertEqual(resp.data['instance_run']['participant_count'], 1)

    def test_rejects_spawn_world_from_another_template_world(self):
        other_world = World.objects.new_world(
            name='Elsewhere',
            author=self.user,
        )
        other_spawn = other_world.create_spawn_world()

        resp = self.client.get(
            reverse(
                'builder-world-admin-instance',
                args=[self.world.pk, other_spawn.pk],
            )
        )

        self.assertEqual(resp.status_code, 404)

    def test_reset_cleans_stopped_spawn_world(self):
        self.spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_STOPPED)

        mob = Mob.objects.create(
            name='Live Wolf',
            world=self.spawn_world,
            room=self.room,
        )
        rock = Item.objects.create(
            name='Ground Rock',
            world=self.spawn_world,
            container=self.room,
        )
        apple = Item.objects.create(
            name='Apple',
            world=self.spawn_world,
            container=self.player,
        )

        resp = self.client.post(self.reset_endpoint)

        self.assertEqual(resp.status_code, 200)
        self.spawn_world.refresh_from_db()
        self.assertEqual(self.spawn_world.lifecycle, api_consts.WORLD_LIFECYCLE_STOPPED)
        self.assertTrue(self.spawn_world.is_clean)
        self.assertFalse(Mob.objects.filter(pk=mob.pk).exists())
        self.assertFalse(Item.objects.filter(pk=rock.pk).exists())
        self.assertTrue(Item.objects.filter(pk=apple.pk).exists())
        self.assertEqual(resp.data['counts']['mobs_loaded'], 0)
        self.assertEqual(resp.data['counts']['items_on_ground'], 0)
        self.assertEqual(resp.data['counts']['items_total'], 1)

    def test_reset_requires_stopped_lifecycle(self):
        self.spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_RUNNING)

        resp = self.client.post(self.reset_endpoint)

        self.assertEqual(resp.status_code, 400)


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
        key = ItemDefinition.objects.create(
            item_type='key',
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
        key = ItemDefinition.objects.create(
            item_type='key',
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
        self.assertFalse(self.player.is_builder)
        resp = self.client.put(
            reverse('builder-player-detail', args=[
                self.world.pk, self.player.pk]),
            {
                'id': self.player.id,
                'is_builder': True
            })
        self.assertEqual(resp.status_code, 200)
        self.player.refresh_from_db()
        self.assertTrue(self.player.is_builder)

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
