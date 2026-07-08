from copy import deepcopy

from builders.models import AbilityDefinition
from tests.base import WorldTestCase
from wr2_tests.utils import (
    BASIC_TEST_STAT_SYSTEM,
    capture_game_messages,
    dispatch_text_command,
)

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

    def _ability(
        self,
        *,
        slug,
        name,
        verbs=None,
        components=None,
        target=None,
        availability=None,
        cost=None,
        cast_time=None,
        cooldown=None,
        help=None,
    ):
        return AbilityDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            command_verbs=verbs or [slug.replace("-", "_")],
            action_type="primary",
            target=target or {
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": False,
            },
            availability=availability or {"classes": [], "min_level": 1},
            requirements={},
            cost=cost or {},
            cast_time=cast_time or {"rounds": 0},
            cooldown=cooldown or {"rounds": 0},
            help=help or {},
            components=components or [
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {},
                    "text": {"label": name},
                },
            ],
        )

    def test_help_lists_available_commands(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertIn("Commands:", message.get("text", ""))
        self.assertIn("look | look <target>", message.get("text", ""))
        self.assertIn("scan <direction>", message.get("text", ""))
        self.assertIn("/load <item|mob> <definition_id|slug> [cmd]", message.get("text", ""))
        self.assertNotIn("/resync", message.get("text", ""))

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
        self.assertNotIn("/load <item|mob> <definition_id|slug> [cmd]", message.get("text", ""))
        self.assertNotIn("/resync", message.get("text", ""))

    def test_help_supports_partial_builder_command_lookup(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help /lo")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "/load")
        self.assertIn("/load <item|mob> <definition_id|slug> [cmd]", message.get("text", ""))

    def test_help_known_ability_uses_authored_help_text(self):
        self._ability(
            slug="bash",
            name="Bash",
            verbs=["bash"],
            help={"text": "A practiced shield hit that stops a foe cold."},
        )
        self.player.known_abilities = ["bash"]
        self.player.save(update_fields=["known_abilities"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help bash")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("text"),
            "Bash - A practiced shield hit that stops a foe cold.",
        )
        self.assertEqual(message["data"]["ability"]["slug"], "bash")
        self.assertEqual(message["data"]["ability"]["help_source"], "authored")
        self.assertNotIn("command", message["data"])

    def test_help_known_ability_generates_plain_text_from_definition(self):
        self._ability(
            slug="bash",
            name="Bash",
            verbs=["bash"],
            cast_time={"rounds": 1},
            cooldown={"rounds": 6, "trigger": "on_hit"},
            components=[
                {
                    "type": "effect",
                    "effect": "stun",
                    "target": "ability.target",
                    "duration": {"rounds": 2},
                    "apply": "on_hit",
                    "text": {"label": "Bash"},
                },
            ],
        )
        self.player.known_abilities = ["bash"]
        self.player.save(update_fields=["known_abilities"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help bash")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("text"),
            "Bash - 1 round cast, 6 round cooldown, stuns the target for 2 rounds if it lands.",
        )
        self.assertEqual(message["data"]["ability"]["help_source"], "generated")

    def test_help_learnable_ability_generates_damage_and_cost_text_with_name(self):
        stat_system = deepcopy(BASIC_TEST_STAT_SYSTEM)
        stat_system.setdefault("labels", {}).setdefault("resources", {})["energy"] = "Ichor"
        self.world.config.stat_system = stat_system
        self.world.config.save(update_fields=["stat_system"])
        self._ability(
            slug="trident",
            name="Trident",
            verbs=["trident"],
            cast_time={"rounds": 1},
            cost={"resource": "energy", "amount": 20, "calc": "percent_base"},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_ability",
                    "overrides": {"multiplier": 1.25},
                    "text": {"label": "Trident"},
                },
            ],
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "h tri")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("text"),
            "Trident - 1 round cast, inflicts 1.25x ability damage on the target. Costs 20% of base ichor.",
        )
        self.assertEqual(message["data"]["ability"]["slug"], "trident")
