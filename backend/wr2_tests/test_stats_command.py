from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command


class TestStatsCommand(WorldTestCase):
    def _message_by_type(self, messages, message_type):
        for msg in messages:
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_stats_command_returns_actor_and_world_snapshot(self):
        self.world.config.stat_system = {
            "labels": {
                "resources": {
                    "energy": "Focus",
                },
                "stats": {
                    "ability_power": "Ability Power",
                },
                "classes": {
                    "warrior": "Vanguard",
                },
            },
        }
        self.world.config.save(update_fields=["stat_system"])

        self.player.health = 17
        self.player.energy = 9
        self.player.stamina = 33
        self.player.save(update_fields=["health", "energy", "stamina"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "stats")

        message = self._message_by_type(messages, "cmd.stats.success")
        self.assertIsNotNone(message)
        self.assertEqual(message.get("text"), "You review your stats.")

        actor = message["data"]["actor"]
        world = message["data"]["world"]

        self.assertEqual(actor["key"], self.player.key)
        self.assertEqual(actor["health"], 17)
        self.assertEqual(actor["energy"], 9)
        self.assertEqual(actor["energy"], 9)
        self.assertEqual(actor["energy_max"], actor["energy_max"])
        self.assertEqual(actor["ability_power"], actor["ability_power"])
        self.assertEqual(actor["experience"], self.player.experience)
        self.assertEqual(actor["experience_needed"], 30)
        self.assertEqual(actor["attributes"], {})
        self.assertIn("attack_power", actor["stats"])

        self.assertEqual(world["labels"]["resources"]["energy"], "Focus")
        self.assertEqual(
            world["labels"]["stats"]["ability_power"],
            "Ability Power",
        )
        self.assertEqual(world["labels"]["classes"]["warrior"], "Vanguard")

    def test_stats_command_uses_combat_rating_percentages(self):
        self.world.config.stat_system = {
            "class_profiles": {},
            "formulas": {
                "base_stats": {
                    "crit": 13,
                    "dodge": 13,
                    "resilience": 19,
                },
            },
        }
        self.world.config.combat_system = {
            "ratings": {
                "crit": {
                    "type": "percentage_points",
                    "base": 0,
                    "cap": 1.0,
                },
                "dodge": {
                    "type": "percentage_points",
                    "base": 0,
                    "cap": 0.75,
                },
                "armor": {
                    "type": "percentage_points",
                    "base": 0,
                    "cap": 0.75,
                },
                "resilience": {
                    "type": "percentage_points",
                    "base": 0,
                    "cap": 0.75,
                },
            },
        }
        self.world.config.save(update_fields=["stat_system", "combat_system"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "stats")

        message = self._message_by_type(messages, "cmd.stats.success")
        self.assertIsNotNone(message)

        actor = message["data"]["actor"]
        world = message["data"]["world"]

        self.assertTrue(world["is_classless"])
        self.assertTrue(world["classless"])
        self.assertEqual(world["combat"]["ratings"]["crit"]["type"], "percentage_points")
        self.assertEqual(actor["stats"]["crit"], 13)
        self.assertEqual(actor["crit_perc"], 13)
        self.assertEqual(actor["stats"]["dodge"], 13)
        self.assertEqual(actor["dodge_perc"], 13)
        self.assertEqual(actor["stats"]["resilience"], 19)
        self.assertEqual(actor["resilience_perc"], 19)
        self.assertEqual(actor["armor_perc"], 0)

    def test_help_stats_topic_is_available(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help stats")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "stats")
        self.assertIn("Show your current vitals", message.get("text", ""))
