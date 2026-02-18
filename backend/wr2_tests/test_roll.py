from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command


class TestRollCommands(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _message_entry(self, messages, message_type, player_key):
        for msg in messages:
            if msg["player_key"] != player_key:
                continue
            if msg["message"].get("type") == message_type:
                return msg
        return None

    def test_roll_default_is_d6(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "roll")

        message = self._message_by_type(messages, "cmd.roll.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["die"], "1d6")
        self.assertGreaterEqual(message["data"]["outcome"], 1)
        self.assertLessEqual(message["data"]["outcome"], 6)
        self.assertIn("You roll 1d6:", message.get("text", ""))

    def test_roll_size_argument_becomes_single_die(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "roll 10")

        message = self._message_by_type(messages, "cmd.roll.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["die"], "1d10")
        self.assertGreaterEqual(message["data"]["outcome"], 1)
        self.assertLessEqual(message["data"]["outcome"], 10)

    def test_roll_descriptor_argument_is_used(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "roll 2d6")

        message = self._message_by_type(messages, "cmd.roll.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["die"], "2d6")
        self.assertGreaterEqual(message["data"]["outcome"], 2)
        self.assertLessEqual(message["data"]["outcome"], 12)

    def test_roll_notifies_other_players_in_room(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "roll 8")

        actor_message = self._message_entry(messages, "cmd.roll.success", self.player.key)
        self.assertIsNotNone(actor_message)

        notify_entry = self._message_entry(
            messages,
            "notification.cmd.roll.success",
            watcher.key,
        )
        self.assertIsNotNone(notify_entry)
        notify_message = notify_entry["message"]
        self.assertEqual(notify_message["data"]["die"], "1d8")
        self.assertGreaterEqual(notify_message["data"]["outcome"], 1)
        self.assertLessEqual(notify_message["data"]["outcome"], 8)
        self.assertIn("Joe rolls 1d8:", notify_message.get("text", ""))

    def test_help_roll_topic(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help roll")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertIn("roll <size> | roll <num>d<size>", message.get("text", ""))
        self.assertEqual(message["data"]["command"]["command"], "roll")
