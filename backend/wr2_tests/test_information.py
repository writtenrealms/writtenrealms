from core.computations import compute_stats
from spawns.handlers import dispatch_command
from django.utils import timezone
from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages


class TestLookCommandText(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_look_success_includes_text(self):
        self.room.description = "A test room."
        self.room.save(update_fields=["description"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="look",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.look.success")
        self.assertIsNotNone(message)
        self.assertTrue(message.get("text"))
        self.assertIn(self.room.name, message["text"])
        self.assertIn("A test room.", message["text"])


class TestStateSyncText(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_state_sync_includes_full_room_text(self):
        self.room.description = "A sync room."
        self.room.save(update_fields=["description"])

        self.player.refresh_from_db()
        if self.player.config:
            self.player.config.room_brief = True
            self.player.config.save(update_fields=["room_brief"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)
        self.assertTrue(message.get("text"))
        self.assertIn(self.room.name, message["text"])
        self.assertIn("A sync room.", message["text"])

    def test_state_sync_who_list_includes_only_in_game_players(self):
        self.player.in_game = True
        self.player.last_action_ts = timezone.now()
        self.player.save(update_fields=["in_game", "last_action_ts"])

        online_user = self.create_user("online@example.com")
        online_player = self.create_player("Online", user=online_user)
        online_player.in_game = True
        online_player.last_action_ts = timezone.now()
        online_player.save(update_fields=["in_game", "last_action_ts"])

        offline_user = self.create_user("offline@example.com")
        offline_player = self.create_player("Offline", user=offline_user)
        offline_player.in_game = False
        offline_player.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)
        who_keys = {entry["key"] for entry in message["data"]["who_list"]}
        self.assertIn(self.player.key, who_keys)
        self.assertIn(online_player.key, who_keys)
        self.assertNotIn(offline_player.key, who_keys)

    def test_state_sync_actor_includes_computed_vital_caps(self):
        stats = compute_stats(self.player.level, self.player.archetype)

        self.player.health = max(stats["health_max"] - 10, 1)
        self.player.mana = max(stats["mana_max"] - 1, 0)
        self.player.stamina = max(stats["stamina_max"] - 1, 0)
        self.player.save(update_fields=["health", "mana", "stamina"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)
        actor = message["data"]["actor"]

        self.assertEqual(actor["health_max"], stats["health_max"])
        self.assertEqual(actor["mana_max"], stats["mana_max"])
        self.assertEqual(actor["stamina_max"], stats["stamina_max"])


class TestStateSyncMapKeys(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_state_sync_actor_room_key_matches_map_when_relative_id_differs(self):
        self.room.relative_id = self.room.id + 5000
        self.room.save(update_fields=["relative_id"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)

        data = message["data"]
        actor_room_key = data["actor"]["room"]["key"]
        room_key = data["room"]["key"]
        map_keys = {room["key"] for room in data["map"]}

        self.assertEqual(actor_room_key, room_key)
        self.assertIn(actor_room_key, map_keys)
        self.assertEqual(actor_room_key, f"room.{self.room.relative_id}")

    def test_state_sync_world_room_refs_use_relative_key(self):
        self.room.relative_id = self.room.id + 9000
        self.room.save(update_fields=["relative_id"])

        self.world.config.starting_room = self.room
        self.world.config.death_room = self.room
        self.world.config.save(update_fields=["starting_room", "death_room"])

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="state.sync",
                player_id=self.player.id,
                payload={},
            )

        message = self._message_by_type(messages, "cmd.state.sync.success")
        self.assertIsNotNone(message)

        world_data = message["data"]["world"]
        expected_key = f"room.{self.room.relative_id}"
        self.assertEqual(world_data["starting_room"], expected_key)
        self.assertEqual(world_data["death_room"], expected_key)
