from copy import deepcopy

from builders.models import AbilityDefinition
from spawns.handlers import get_handler, iter_text_handlers
from tests.base import WorldTestCase
from tests.utils import (
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
    "glory",
)
EXPECTED_SET_MOB_FIELDS = (
    "name",
    "room_description",
    "description",
    "level",
    "experience",
    "health",
    "energy",
    "stamina",
    "attributes",
    "aggression",
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
        self.assertIn("look | look [at|in|inside|into] <target|direction>", message.get("text", ""))
        self.assertIn("scan <direction>", message.get("text", ""))
        self.assertIn("/load <item|mob> <definition_id|slug> [cmd]", message.get("text", ""))
        self.assertIn(
            "/transfer <target> <room@relative_id|relative_id|direction|here>",
            message.get("text", ""),
        )
        self.assertIn(
            "/edit [database_id|room.database_id|room@relative_id]",
            message.get("text", ""),
        )
        self.assertIn("/repop", message.get("text", ""))
        self.assertNotIn("/resync", message.get("text", ""))

        commands = message["data"]["commands"]
        self.assertTrue(any(entry["command"] == "help" for entry in commands))
        self.assertTrue(any(entry["command"] == "/edit" for entry in commands))

    def test_help_specific_command_uses_optional_argument(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help drop")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertIn("Drop", message.get("text", ""))
        self.assertIn("drop <item>", message.get("text", ""))
        self.assertEqual(message["data"]["command"]["command"], "drop")
        self.assertNotIn("help", message["data"])

    def test_help_index_covers_every_registered_route_without_database_queries(self):
        handler = get_handler("help")
        for include_builder in (False, True):
            with self.subTest(include_builder=include_builder):
                with self.assertNumQueries(0):
                    entries = handler._build_command_list(include_builder)
                indexed = [command for entry in entries for command in entry["commands"]]
                routes = dict(iter_text_handlers(include_builder=include_builder))
                self.assertCountEqual(indexed, routes)
                self.assertEqual(len(indexed), len(set(indexed)))
                for entry in entries:
                    self.assertTrue(entry["category"])
                    self.assertTrue(entry["description"])
                    for command in entry["commands"]:
                        self.assertEqual(
                            entry["category"] == "Builder Commands",
                            routes[command].builder_only,
                        )

    def test_every_indexed_command_opens_help(self):
        for command, _handler in iter_text_handlers():
            with self.subTest(command=command), capture_game_messages() as messages:
                dispatch_text_command(self.player.id, f"help {command}")
                message = self._message_by_type(messages, "cmd.help.success")
                self.assertIsNotNone(message)
                self.assertEqual(message["data"]["command"]["command"], command)

    def test_builder_flag_without_world_edit_access_does_not_expose_builder_help(self):
        user = self.create_user("former-builder@example.com")
        player = self.create_player("Former", user=user)
        player.is_builder = True
        player.save(update_fields=["is_builder"])

        with capture_game_messages() as messages:
            dispatch_text_command(player.id, "help")
        message = self._message_by_type(messages, "cmd.help.success")
        self.assertFalse(any(
            entry["category"] == "Builder Commands"
            for entry in message["data"]["commands"]
        ))
        with capture_game_messages() as messages:
            dispatch_text_command(player.id, "help /set")
        self.assertIsNotNone(self._message_by_type(messages, "cmd.help.error"))

    def test_help_describes_current_discovery_and_merchant_syntax(self):
        for command, snippets in {
            "help": ("help <command|ability>", "can currently learn", "socials", "Room and quest"),
            "who": ("visible players", "your world", "highlighted"),
            "buy": ("buy | buy <number|item>", "current list or shop view"),
            "sell": ("sell | sell <number|item>", "current offer view"),
            "buyback": ("buyback [merchant]",),
            "craft": ("currency fee", "rolled stats"),
            "time": ("without an argument", "time control enabled"),
        }.items():
            with self.subTest(command=command), capture_game_messages() as messages:
                dispatch_text_command(self.player.id, f"help {command}")
                message = self._message_by_type(messages, "cmd.help.success")
                self.assertIsNotNone(message)
                for snippet in snippets:
                    self.assertIn(snippet, message["text"])

    def test_help_edit_describes_room_identity_and_new_tab_behavior(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help /edit")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "/edit")
        text = message.get("text", "")
        self.assertIn("Edit Room", text)
        self.assertIn(
            "/edit [database_id|room.database_id|room@relative_id]",
            text,
        )
        self.assertIn("current room", text)
        self.assertIn("bare positive number", text)
        self.assertIn("database ID", text)
        self.assertIn("canonical, portable room", text)
        self.assertIn("room.database_id", text)
        self.assertIn("current authored world", text)
        self.assertIn("new browser tab", text)

    def test_help_eq_alias_shows_equipment_topic(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help eq")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "equipment")
        self.assertIn("Show items currently equipped", message.get("text", ""))

    def test_help_loot_shows_get_topic(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help loot")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "loot")
        self.assertIn("get all corpse", message.get("text", ""))
        self.assertIn("get all all.corpse", message.get("text", ""))

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

    def test_help_repop_describes_forced_zone_reconciliation(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help /repop")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "/repop")
        self.assertIn("/repop [--doors]", message.get("text", ""))
        self.assertIn("current zone", message.get("text", ""))
        self.assertIn("respawn.mode: none", message.get("text", ""))
        self.assertIn("not duplicated", message.get("text", ""))
        self.assertIn("unchanged by default", message.get("text", ""))
        self.assertIn("authored defaults", message.get("text", ""))

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
        self.assertNotIn(
            "/edit [database_id|room.database_id|room@relative_id]",
            message.get("text", ""),
        )
        self.assertNotIn("/repop", message.get("text", ""))
        self.assertNotIn("/resync", message.get("text", ""))

    def test_help_supports_partial_builder_command_lookup(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help /lo")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "/load")
        self.assertIn("/load <item|mob> <definition_id|slug> [cmd]", message.get("text", ""))

    def test_help_resolves_legacy_transfer_alias(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help transfer")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "/transfer")
        self.assertIn("Use room@relative_id", message.get("text", ""))

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

    def test_help_hides_mob_only_ability_even_when_stale_known(self):
        self._ability(
            slug="mob-curse",
            name="Mob Curse",
            verbs=["mobcurse"],
            availability={
                "actors": ["mob"],
                "classes": [],
                "min_level": 1,
            },
        )
        self.player.known_abilities = ["mob-curse"]
        self.player.save(update_fields=["known_abilities"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help mob curse")

        self.assertIsNone(self._message_by_type(messages, "cmd.help.success"))
        error = self._message_by_type(messages, "cmd.help.error")
        self.assertIsNotNone(error)
        self.assertEqual(error["text"], "Unknown command: mob curse")

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

    def test_help_known_ability_describes_on_hit_interrupt_in_component_order(self):
        self._ability(
            slug="kick",
            name="Kick",
            verbs=["kick"],
            cooldown={"rounds": 12},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 0.25},
                    "text": {"label": "Kick"},
                },
                {
                    "type": "interrupt",
                    "target": "ability.target",
                    "apply": "on_hit",
                    "text": {"label": "Kick"},
                },
            ],
        )
        self.player.known_abilities = ["kick"]
        self.player.save(update_fields=["known_abilities"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help kick")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("text"),
            "Kick - 12 round cooldown, inflicts 0.25x physical damage on the "
            "target, interrupts the target's active cast or channel if it lands.",
        )
        self.assertEqual(message["data"]["ability"]["help_source"], "generated")

    def test_help_known_ability_describes_flee_prevention_action_rule(self):
        self._ability(
            slug="mob-leg-irons",
            name="Leg Irons",
            verbs=["graspingroots"],
            cast_time={"rounds": 1},
            cooldown={"rounds": 7},
            components=[
                {
                    "type": "effect",
                    "effect": "root",
                    "category": "debuff",
                    "target": "ability.target",
                    "duration": {"rounds": 4},
                    "apply": "on_resolve",
                    "primitives": [
                        {
                            "type": "action_rule",
                            "phase": "before_action",
                            "rule": "prevent",
                            "actions": ["flee"],
                            "reason": "rooted",
                        },
                    ],
                    "text": {"label": "Rooted"},
                },
            ],
        )
        self.player.known_abilities = ["mob-leg-irons"]
        self.player.save(update_fields=["known_abilities"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help graspingroots")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(
            message.get("text"),
            "Leg Irons - 1 round cast, 7 round cooldown, prevents the target "
            "from fleeing for 4 rounds.",
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
