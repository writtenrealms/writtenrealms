from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.actions.information import LookAction
from spawns.handlers import dispatch_command
from spawns.models import Item, Mob
from spawns.state_payloads import build_state_sync
from tests.base import WorldTestCase
from tests.utils import capture_game_messages, dispatch_text_command
from worlds.models import World, WorldConfig


class InstanceRuntimeIsolationTests(WorldTestCase):
    """Authored rooms are shared, but their live contents never are."""

    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=["is_multiplayer"])
        self.world.create_spawn_world()
        instance_config = WorldConfig.objects.create()
        self.instance_template = World.objects.new_world(
            name="Parallel Arena",
            author=self.user,
            config=instance_config,
            instance_of=self.world,
        )
        self.arena_room = self.instance_template.config.starting_room
        self.east_room = self.arena_room.create_at(adv_consts.DIRECTION_EAST)
        self.runtime_a = self.instance_template.create_spawn_world(
            instance_ref="arena-a",
        )
        self.runtime_b = self.instance_template.create_spawn_world(
            instance_ref="arena-b",
        )
        self.player_a = self.create_player(
            "Alpha",
            user=self.user,
            world=self.runtime_a,
            room=self.arena_room,
        )
        self.player_b = self.create_player(
            "Bravo",
            user=self.create_user("bravo@example.com"),
            world=self.runtime_b,
            room=self.arena_room,
        )
        self.watcher_a = self.create_player(
            "Ally",
            user=self.create_user("ally@example.com"),
            world=self.runtime_a,
            room=self.arena_room,
        )
        for player in (self.player_a, self.player_b, self.watcher_a):
            player.in_game = True
            player.stamina = 20
            player.save(update_fields=["in_game", "stamina"])

    @staticmethod
    def _message_for(messages, message_type, player_key):
        return next(
            (
                entry["message"]
                for entry in messages
                if entry["player_key"] == player_key
                and entry["message"].get("type") == message_type
            ),
            None,
        )

    def test_state_sync_only_serializes_live_contents_from_players_runtime(self):
        mob_a = Mob.objects.create(
            world=self.runtime_a,
            room=self.arena_room,
            name="Alpha Guard",
            keywords="alphaguard",
        )
        Mob.objects.create(
            world=self.runtime_b,
            room=self.arena_room,
            name="Bravo Guard",
            keywords="bravoguard",
        )
        item_a = Item.objects.create(
            world=self.runtime_a,
            container=self.arena_room,
            name="Alpha Token",
            keywords="alphatoken",
        )
        Item.objects.create(
            world=self.runtime_b,
            container=self.arena_room,
            name="Bravo Token",
            keywords="bravotoken",
        )

        room_payload = build_state_sync(self.player_a).model_dump()["room"]
        char_keys = {char["key"] for char in room_payload["chars"]}
        item_keys = {item["key"] for item in room_payload["inventory"]}

        self.assertIn(self.player_a.key, char_keys)
        self.assertIn(self.watcher_a.key, char_keys)
        self.assertIn(mob_a.key, char_keys)
        self.assertNotIn(self.player_b.key, char_keys)
        self.assertEqual(item_keys, {item_a.key})

    def test_look_cannot_target_player_mob_or_item_from_parallel_runtime(self):
        Mob.objects.create(
            world=self.runtime_b,
            room=self.arena_room,
            name="Bravo Guard",
            keywords="bravoguard",
        )
        Item.objects.create(
            world=self.runtime_b,
            container=self.arena_room,
            name="Bravo Token",
            keywords="bravotoken",
        )

        for selector in ("bravo", "bravoguard", "bravotoken"):
            with self.assertRaises(ActionError) as raised:
                LookAction().execute(self.player_a.id, selector)
            self.assertEqual(raised.exception.code, "target_not_found")

    def test_builder_look_only_serializes_current_runtime_contents(self):
        self.player_a.is_builder = True
        self.player_a.save(update_fields=["is_builder"])
        own_mob = Mob.objects.create(
            world=self.runtime_a,
            room=self.arena_room,
            name="Alpha Guard",
            keywords="alphaguard",
        )
        foreign_mob = Mob.objects.create(
            world=self.runtime_b,
            room=self.arena_room,
            name="Bravo Guard",
            keywords="bravoguard",
        )
        own_item = Item.objects.create(
            world=self.runtime_a,
            container=self.arena_room,
            name="Alpha Token",
            keywords="alphatoken",
        )
        foreign_item = Item.objects.create(
            world=self.runtime_b,
            container=self.arena_room,
            name="Bravo Token",
            keywords="bravotoken",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player_a.id, "look")

        look = self._message_for(messages, "cmd.look.success", self.player_a.key)
        self.assertIsNotNone(look)
        char_keys = {char["key"] for char in look["data"]["target"]["chars"]}
        item_keys = {item["key"] for item in look["data"]["target"]["inventory"]}
        self.assertIn(own_mob.key, char_keys)
        self.assertNotIn(foreign_mob.key, char_keys)
        self.assertIn(own_item.key, item_keys)
        self.assertNotIn(foreign_item.key, item_keys)

    def test_builder_purge_only_mutates_and_serializes_current_runtime(self):
        self.player_a.is_builder = True
        self.player_a.save(update_fields=["is_builder"])
        own_mob = Mob.objects.create(
            world=self.runtime_a,
            room=self.arena_room,
            name="Alpha Guard",
            keywords="alphaguard",
        )
        foreign_mob = Mob.objects.create(
            world=self.runtime_b,
            room=self.arena_room,
            name="Bravo Guard",
            keywords="bravoguard",
        )
        own_item = Item.objects.create(
            world=self.runtime_a,
            container=self.arena_room,
            name="Alpha Token",
            keywords="alphatoken",
        )
        foreign_item = Item.objects.create(
            world=self.runtime_b,
            container=self.arena_room,
            name="Bravo Token",
            keywords="bravotoken",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player_a.id, "/pu")

        purge = self._message_for(
            messages,
            "cmd./purge.success",
            self.player_a.key,
        )
        self.assertIsNotNone(purge, messages)
        self.assertFalse(Mob.objects.filter(pk=own_mob.pk).exists())
        self.assertFalse(Item.objects.filter(pk=own_item.pk).exists())
        self.assertTrue(Mob.objects.filter(pk=foreign_mob.pk).exists())
        self.assertTrue(Item.objects.filter(pk=foreign_item.pk).exists())

        char_keys = {char["key"] for char in purge["data"]["room"]["chars"]}
        item_keys = {item["key"] for item in purge["data"]["room"]["inventory"]}
        self.assertNotIn(foreign_mob.key, char_keys)
        self.assertNotIn(foreign_item.key, item_keys)

    def test_say_yell_and_emote_only_fan_out_within_runtime_world(self):
        commands = (
            ("say hello", "notification.cmd.say.success"),
            ("yell hello", "notification.cmd.yell.success"),
            ("emote waves.", "notification.cmd.emote.success"),
        )

        for command, notification_type in commands:
            with capture_game_messages() as messages:
                dispatch_text_command(self.player_a.id, command)

            self.assertIsNotNone(
                self._message_for(messages, notification_type, self.watcher_a.key),
                command,
            )
            self.assertIsNone(
                self._message_for(messages, notification_type, self.player_b.key),
                command,
            )

    def test_movement_payload_and_notifications_stay_within_runtime_world(self):
        destination_watcher_a = self.create_player(
            "East Ally",
            user=self.create_user("east-ally@example.com"),
            world=self.runtime_a,
            room=self.east_room,
        )
        destination_watcher_b = self.create_player(
            "East Bravo",
            user=self.create_user("east-bravo@example.com"),
            world=self.runtime_b,
            room=self.east_room,
        )
        for player in (destination_watcher_a, destination_watcher_b):
            player.in_game = True
            player.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="move",
                player_id=self.player_a.id,
                payload={"direction": adv_consts.DIRECTION_EAST},
            )

        move = self._message_for(messages, "cmd.move.success", self.player_a.key)
        self.assertIsNotNone(move)
        room_char_keys = {char["key"] for char in move["data"]["room"]["chars"]}
        self.assertIn(destination_watcher_a.key, room_char_keys)
        self.assertNotIn(destination_watcher_b.key, room_char_keys)

        self.assertIsNotNone(
            self._message_for(
                messages,
                "notification.movement.exit",
                self.watcher_a.key,
            )
        )
        self.assertIsNone(
            self._message_for(
                messages,
                "notification.movement.exit",
                self.player_b.key,
            )
        )
        self.assertIsNotNone(
            self._message_for(
                messages,
                "notification.movement.enter",
                destination_watcher_a.key,
            )
        )
        self.assertIsNone(
            self._message_for(
                messages,
                "notification.movement.enter",
                destination_watcher_b.key,
            )
        )

    def test_room_item_selection_payloads_and_events_stay_within_runtime(self):
        foreign_item = Item.objects.create(
            world=self.runtime_b,
            container=self.arena_room,
            name="Bravo Token",
            keywords="bravotoken",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player_a.id, "get bravotoken")

        error = self._message_for(messages, "cmd.get.error", self.player_a.key)
        self.assertIsNotNone(error)
        self.assertEqual(error["data"]["code"], "empty_room")
        foreign_item.refresh_from_db()
        self.assertEqual(foreign_item.container_id, self.arena_room.id)

        own_item = Item.objects.create(
            world=self.runtime_a,
            container=self.player_a,
            name="Alpha Token",
            keywords="alphatoken",
        )
        with capture_game_messages() as messages:
            dispatch_text_command(self.player_a.id, "drop alphatoken")

        drop = self._message_for(messages, "cmd.drop.success", self.player_a.key)
        self.assertIsNotNone(drop)
        room_item_keys = {item["key"] for item in drop["data"]["room"]["inventory"]}
        self.assertIn(own_item.key, room_item_keys)
        self.assertNotIn(foreign_item.key, room_item_keys)
        self.assertIsNotNone(
            self._message_for(
                messages,
                "notification.cmd.drop.success",
                self.watcher_a.key,
            )
        )
        self.assertIsNone(
            self._message_for(
                messages,
                "notification.cmd.drop.success",
                self.player_b.key,
            )
        )
