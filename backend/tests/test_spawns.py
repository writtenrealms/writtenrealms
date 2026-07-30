import mock

from datetime import timedelta

from config import constants as adv_consts

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from rest_framework.reverse import reverse

from config import constants as api_consts
from backend.config.exceptions import ServiceError
from builders.models import MobDefinition, Trigger
from spawns import serializers as spawns_serializers
from spawns.events import (
    PLAYER_ROOM_ENTER_EVENT_TYPE,
    flush_game_event_outbox,
)
from spawns.models import (
    GameEventOutbox,
    Player,
    PlayerConfig,
    Item,
    Equipment,
    Mob)
from spawns.services import WorldGate
from system.models import IntroConfig
from tests.base import WorldTestCase
from tests.utils import capture_game_messages
from users.models import User
from worlds.models import Room


class TestDeletions(WorldTestCase):

    def setUp(self):
        super().setUp()
        self.spawn_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            world=self.spawn_world,
            room=self.room,
            user=self.user,
            name='John')
        self.item = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            name='a rock')

    def test_delete_equipment(self):
        "Tests that deleting players and mobs deletes their respective eq"
        eq = self.player.equipment
        self.player.delete()
        with self.assertRaises(Equipment.DoesNotExist):
            Equipment.objects.get(pk=eq.pk)

        mob = Mob.objects.create(world=self.spawn_world, room=self.room)
        eq = mob.equipment
        mob.delete()
        with self.assertRaises(Equipment.DoesNotExist):
            Equipment.objects.get(pk=eq.pk)

    def test_delete_item_in_inventory(self):
        "Ensure deleting an item in a player's inv doesn't remove the player"
        self.item.delete()
        player = Player.objects.get(pk=self.player.pk)

    def test_equipped_item(self):
        self.player.equipment.weapon = self.item
        self.player.equipment.save()
        eq = self.player.equipment
        self.item.delete()
        eq = Equipment.objects.get(pk=eq.pk)

    def test_delete_item_in_room(self):
        self.item.container = self.room
        self.item.save()
        self.item.delete()
        room = Room.objects.get(pk=self.room.pk)

    def test_delete_spw_after_moving_player(self):
        # Bug I ran into with spw reset
        self.assertFalse(self.spawn_world.is_multiplayer)

        player = self.player
        other_room = player.room.create_at('east')
        player.viewed_rooms.add(other_room)

        original_world = self.spawn_world
        new_world = self.spawn_world.context.create_spawn_world()

        player.world = new_world
        player.room = new_world.config.starting_room
        player.save()
        player.initialize(reset=True)

        original_world.delete()

        player = Player.objects.get(pk=player.pk)

    def test_reset_enqueues_room_enter_for_new_runtime_world(self):
        player = self.player
        origin_room = player.room.create_at('east')
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.world.config.starting_room_id,
            event=adv_consts.TRIGGER_EVENT_ENTER,
            script='/cmd room -- /echo -- The reset threshold opens.',
            display_action_in_room=False,
            gate_delay=0,
        )
        player.room = origin_room
        player.in_game = True
        player.save(update_fields=['room', 'in_game'])
        original_runtime_id = player.world_id
        original_location_sequence = player.location_sequence

        player = player.reset()
        player.refresh_from_db()

        self.assertNotEqual(player.world_id, original_runtime_id)
        self.assertEqual(player.room_id, self.world.config.starting_room_id)
        self.assertEqual(
            player.location_sequence,
            original_location_sequence + 1,
        )
        event = GameEventOutbox.objects.get(
            event_type=PLAYER_ROOM_ENTER_EVENT_TYPE,
        )
        self.assertEqual(event.data['actor']['key'], player.key)
        self.assertEqual(event.data['origin_room']['id'], origin_room.id)
        self.assertEqual(
            event.data['destination_room']['id'],
            self.world.config.starting_room_id,
        )
        self.assertEqual(event.data['runtime_world_id'], player.world_id)
        self.assertEqual(
            event.data['location_sequence'],
            player.location_sequence,
        )
        self.assertEqual(event.data['source'], 'character_reset')

        with capture_game_messages() as messages:
            flush_game_event_outbox()

        reset_echoes = [
            entry['message']
            for entry in messages
            if entry['message'].get('type') == 'cmd./echo.success'
            and 'reset threshold' in entry['message'].get('text', '')
        ]
        self.assertEqual(len(reset_echoes), 1)

    def test_delete_mob_with_inventory(self):
        """
        Regression test. Because mobs that load with equipment
        may get looted, and because we clean up mobs from the API
        that have died, items being synced during players extraction
        sometimes run into reference errors on the API side.
        """
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name='a soldier')
        mob = mob_definition.spawn(self.room, self.spawn_world)
        Item.objects.create(
            world=self.spawn_world,
            container=mob,
            name='an apple')

        corpse = mob.inventory.filter(
            type=adv_consts.ITEM_TYPE_CORPSE)
        # items_pks = mob.inventory.values_list('pk', flat=True)
        # self.assertEqual(len(items_pks), 2)

        prior_items_num = Item.objects.count()
        mob.delete()
        self.assertEqual(Item.objects.count(), prior_items_num)

    def test_delete_mob_with_equipment(self):
        "Equipment flavor of the above test"
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name='a soldier')
        mob = mob_definition.spawn(self.room, self.spawn_world)

        helmet = Item.objects.create(
            world=self.spawn_world,
            container=mob,
            name='a helmet')

        mob.equipment.equip(helmet, 'head')

        prior_items_num = Item.objects.count()
        mob.delete()
        self.assertEqual(Item.objects.count(), prior_items_num)


class TestCorpseSpawnSerialization(WorldTestCase):
    """
    Test for bug in single player worlds where corpses aren't animating
    properly.
    """

    def test_corpse_serialization(self):
        spawn_world = self.world.create_spawn_world()

        item = Item.objects.create(
            name='the corpse of a spider',
            type='corpse',
            room_description='The corpse of a spider is lying here.',
            world=spawn_world,
            container=self.room)

        data = spawns_serializers.AnimateItemSerializer(item).data
        self.assertEqual(data['name'], 'the corpse of a spider')
        self.assertEqual(
            data['room_description'],
            'The corpse of a spider is lying here.',
        )
        self.assertNotIn('ground_description', data)


class TestOneButtonPlay(WorldTestCase):

    def setUp(self):
        super().setUp()
        IntroConfig.objects.create(world=self.world)

    def test_play(self):
        resp = self.client.get(reverse('logged-in-user'))
        self.assertEqual(resp.status_code, 401)

        resp = self.client.post(reverse('game-play'), {})
        self.assertEqual(resp.status_code, 201)

        player = Player.objects.get(pk=resp.data['player']['id'])
        self.assertEqual(resp.data['player']['key'], player.key)
        self.assertEqual(player.world.context, self.world)

        token = resp.data['token']
        self.assertEqual(len(token.split('.')), 3)

        # Make sure that they can access a protected resource
        self.client.credentials(HTTP_AUTHORIZATION='JWT %s' % token)
        resp = self.client.get(reverse('logged-in-user'))
        self.assertEqual(resp.status_code, 200)


class TestPlayerConfig(WorldTestCase):

    def setUp(self):
        super().setUp()
        spawn_world = self.world.create_spawn_world()
        self.player = Player.objects.create(
            world=spawn_world,
            name='Player',
            room=self.room,
            user=self.user)
        self.client.force_authenticate(self.user)

        self.player.in_game = True
        self.player.save()

        self.headers = {'HTTP_X_PLAYER_ID': self.player.id}

    def use_non_one_shared_default(self):
        current_default = self.player.config
        shared_default = PlayerConfig.objects.create()
        PlayerConfig.objects.filter(pk=shared_default.pk).update(
            created_ts=current_default.created_ts - timedelta(seconds=1))
        shared_default.refresh_from_db()

        self.player.config = shared_default
        self.player.save(update_fields=['config'])
        other_player = Player.objects.create(
            world=self.player.world,
            name='Other player',
            room=self.player.room,
            user=self.user)

        self.assertNotEqual(shared_default.pk, 1)
        self.assertEqual(other_player.config_id, shared_default.pk)
        return shared_default

    def test_valid_edit_clones_shared_default_with_non_one_pk(self):
        default_config = self.use_non_one_shared_default()
        config_count = PlayerConfig.objects.count()

        resp = self.client.post(reverse('game-player-config'), {
            'room_brief': True,
            'combat_brief': True,
            'idle_logout': False,
        }, **self.headers)
        self.assertEqual(resp.status_code, 201)

        self.player.refresh_from_db()
        self.assertNotEqual(self.player.config_id, default_config.pk)
        self.assertEqual(PlayerConfig.objects.count(), config_count + 1)
        self.assertTrue(self.player.config.room_brief)
        self.assertTrue(self.player.config.combat_brief)
        self.assertFalse(self.player.config.idle_logout)
        default_config.refresh_from_db()
        self.assertFalse(default_config.room_brief)
        self.assertFalse(default_config.combat_brief)
        self.assertTrue(default_config.idle_logout)

        # Change once-set config
        player_config_id = self.player.config_id
        resp = self.client.post(reverse('game-player-config'), {
            'room_brief': False,
            'combat_brief': True
        }, **self.headers)
        self.assertEqual(resp.status_code, 201)
        self.player.refresh_from_db()
        self.assertEqual(self.player.config_id, player_config_id)
        self.assertEqual(PlayerConfig.objects.count(), config_count + 1)
        self.assertFalse(self.player.config.room_brief)
        self.assertTrue(self.player.config.combat_brief)

    def test_invalid_edit_does_not_clone_or_reassign_shared_default(self):
        default_config = self.use_non_one_shared_default()
        config_count = PlayerConfig.objects.count()

        resp = self.client.post(reverse('game-player-config'), {
            'mobile_map_width': -1,
        }, **self.headers)

        self.assertEqual(resp.status_code, 400)
        self.player.refresh_from_db()
        self.assertEqual(self.player.config_id, default_config.pk)
        self.assertEqual(PlayerConfig.objects.count(), config_count)


# Service Tests

class EnterWorldTests(WorldTestCase):

    def test_cannot_enter_storing_spw(self):
        self.spawn_world.lifecycle = api_consts.WORLD_STATE_STORING
        self.spawn_world.save()
        with self.assertRaises(ServiceError) as context:
            WorldGate(world=self.spawn_world, player=self.player).enter()
        self.assertEqual(str(context.exception),
                        "World cannot be entered in 'storing' state.")

    @mock.patch('config.game_settings.IS_CLUSTER', False)
    def test_cannot_log_in_multiple_chars_to_mpw(self):
        self.world.is_multiplayer = True
        self.world.save()
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save()

        # Enter with 1st player
        WorldGate(world=self.spawn_world, player=self.player).enter()

        # Create 2nd player and attempt to also enter the world
        player2 = self.create_player('Joe2')
        with self.assertRaises(ServiceError) as context:
            WorldGate(world=self.spawn_world, player=player2).enter()
        self.assertEqual(str(context.exception),
                         "You are logged on another character.")

        # Also test that linked users are taken into account for login
        self.user.link_id = 1
        self.user.save()
        player2.in_game = False
        player2.save()
        second_account = User.objects.create_user(
            'second@example.com', 'p',
            link_id=1)
        player3 = Player.objects.create(
            world=self.spawn_world,
            name='Third',
            room=self.room,
            user=second_account,
            in_game=True,
            last_action_ts=timezone.now())

        with self.assertRaises(ServiceError) as context:
            WorldGate(world=self.spawn_world, player=player3).enter()
        self.assertEqual(str(context.exception),
                         "You are logged on another character.")
