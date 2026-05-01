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
                "derived": {
                    "ability_power": "Skill Power",
                },
                "classes": {
                    "warrior": "Vanguard",
                },
            },
        }
        self.world.config.save(update_fields=["stat_system"])

        self.player.health = 17
        self.player.mana = 9
        self.player.stamina = 33
        self.player.save(update_fields=["health", "mana", "stamina"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "stats")

        message = self._message_by_type(messages, "cmd.stats.success")
        self.assertIsNotNone(message)
        self.assertEqual(message.get("text"), "You review your stats.")

        actor = message["data"]["actor"]
        world = message["data"]["world"]

        self.assertEqual(actor["key"], self.player.key)
        self.assertEqual(actor["health"], 17)
        self.assertEqual(actor["mana"], 9)
        self.assertEqual(actor["energy"], 9)
        self.assertEqual(actor["energy_max"], actor["mana_max"])
        self.assertEqual(actor["ability_power"], actor["spell_power"])
        self.assertEqual(actor["experience"], self.player.experience)
        self.assertEqual(actor["experience_needed"], 30)
        self.assertIn("strength", actor["primary_attributes"])
        self.assertIn("attack_power", actor["derived_stats"])

        self.assertEqual(world["labels"]["resources"]["energy"], "Focus")
        self.assertEqual(
            world["labels"]["derived"]["ability_power"],
            "Skill Power",
        )
        self.assertEqual(world["labels"]["classes"]["warrior"], "Vanguard")

    def test_help_stats_topic_is_available(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "help stats")

        message = self._message_by_type(messages, "cmd.help.success")
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["command"]["command"], "stats")
        self.assertIn("Show your current vitals", message.get("text", ""))
