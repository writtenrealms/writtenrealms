from importlib import import_module

from django.test import SimpleTestCase


migration = import_module("spawns.migrations.0138_active_effects")


class TestActiveEffectMigrationNormalization(SimpleTestCase):
    def test_skips_malformed_and_inactive_payloads(self):
        self.assertIsNone(migration._effect_values("not-a-mapping", world_id=1))
        self.assertIsNone(
            migration._effect_values(
                {"effect": "dot", "remaining_rounds": 0},
                world_id=1,
            )
        )

    def test_reconstructs_legacy_periodic_duration(self):
        values = migration._effect_values(
            {
                "effect": "dot",
                "remaining_rounds": 2,
                "rounds_elapsed": 2,
                "tick": {"every_rounds": 1},
            },
            world_id=1,
            encounter_id=3,
        )

        self.assertEqual(values["remaining_rounds"], 2)
        self.assertEqual(values["duration_rounds"], 4)
        self.assertEqual(values["scope"], "character")

    def test_safely_bounds_legacy_values(self):
        values = migration._effect_values(
            {
                "effect": "A badly formed effect " * 20,
                "stack_key": "oversized stack " * 30,
                "remaining_rounds": "not-a-number",
                "rounds_elapsed": "also-invalid",
                "source": {"type": "mob", "id": "invalid"},
            },
            world_id=1,
        )

        self.assertLessEqual(len(values["effect"]), 120)
        self.assertLessEqual(len(values["stack_key"]), 120)
        self.assertEqual(values["remaining_rounds"], 1)
        self.assertIsNone(values["source_mob_id"])
