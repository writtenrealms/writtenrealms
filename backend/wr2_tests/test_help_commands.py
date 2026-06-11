from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command

EXPECTED_SET_PLAYER_FIELDS = (
    "level",
    "experience",
    "health",
    "energy",
    "stamina",
    "attributes",
    "gold",
    "glory",
    "medals",
)
EXPECTED_SET_MOB_FIELDS = (
    "level",
    "experience",
    "health",
    "energy",
    "stamina",
    "attributes",
    "aggression",
    "gold",
    "exp_worth",
    "health_max",
    "health_regen",
    "energy_max",
    "energy_regen",
    "stamina_max",
    "stamina_regen",
    "armor",
    "dodge",
    "crit",
    "resilience",
    "attack_power",
    "ability_power",
)


class TestHelpCommands(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])

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
        self.assertIn("scan <direction>", message.get("text", ""))
        self.assertIn("/load <item|mob> <template_id|slug> [cmd]", message.get("text", ""))
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

    def test_help_eq_alias_shows_equipment_topic(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help eq")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "equipment")
        self.assertIn("Show items currently equipped", message.get("text", ""))

    def test_help_set_lists_settable_fields(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help /set")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        text = message.get("text", "")
        self.assertIn("Player fields:", text)
        self.assertIn("Mob fields:", text)
        self.assertIn("Attribute keys can be set with", text)
        self.assertIn("Current resources cannot exceed their max", text)
        self.assertIn("Lowering a mob resource max clamps", text)
        for field_name in EXPECTED_SET_PLAYER_FIELDS:
            self.assertIn(field_name, text)
        for field_name in EXPECTED_SET_MOB_FIELDS:
            self.assertIn(field_name, text)

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
        self.assertNotIn("/load <item|mob> <template_id|slug> [cmd]", message.get("text", ""))
        self.assertNotIn("/resync <item|mob> <template_id|all>", message.get("text", ""))

    def test_help_supports_partial_builder_command_lookup(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help /lo")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "/load")
        self.assertIn("/load <item|mob> <template_id|slug> [cmd]", message.get("text", ""))

    def test_help_supports_resync_builder_command_lookup(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help /res")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "/resync")
        self.assertIn("/resync <item|mob> <template_id|all>", message.get("text", ""))
