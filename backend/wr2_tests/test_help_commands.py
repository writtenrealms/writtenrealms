from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command


class TestHelpCommands(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_help_lists_available_commands(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertIn("Commands:", message.get("text", ""))
        self.assertIn("look | look <target>", message.get("text", ""))
        self.assertIn("/load <item|mob> <template_id> [cmd]", message.get("text", ""))
        self.assertIn("/resync <item|mob> <template_id|all>", message.get("text", ""))

        commands = message["data"]["commands"]
        self.assertTrue(any(entry["command"] == "help" for entry in commands))

    def test_help_specific_command_uses_optional_argument(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help drop")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertIn("Drop", message.get("text", ""))
        self.assertIn("drop <item>", message.get("text", ""))
        self.assertEqual(message["data"]["command"]["command"], "drop")
        self.assertNotIn("help", message["data"])

    def test_help_builder_command_requires_builder_permissions(self):
        other_user = self.create_user("other@example.com")
        other_player = self.create_player("Other", user=other_user)

        with capture_game_messages() as messages:
            dispatch_text_command(other_player.id, "help /load")

        message = self._message_by_type(messages, "cmd.help.error")
        self.assertIsNotNone(message)
        self.assertIn("permission", message.get("text", "").lower())

    def test_help_non_builder_list_hides_builder_commands(self):
        other_user = self.create_user("viewer@example.com")
        other_player = self.create_player("Viewer", user=other_user)

        with capture_game_messages() as messages:
            dispatch_text_command(other_player.id, "help")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertNotIn("/load <item|mob> <template_id> [cmd]", message.get("text", ""))
        self.assertNotIn("/resync <item|mob> <template_id|all>", message.get("text", ""))

    def test_help_supports_partial_builder_command_lookup(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help /lo")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "/load")
        self.assertIn("/load <item|mob> <template_id> [cmd]", message.get("text", ""))

    def test_help_supports_resync_builder_command_lookup(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help /res")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "/resync")
        self.assertIn("/resync <item|mob> <template_id|all>", message.get("text", ""))
