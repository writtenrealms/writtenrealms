from tests.base import WorldTestCase


class TestLevelingConfig(WorldTestCase):
    def test_initialize_uses_world_starting_level(self):
        self.world.config.starting_level = 3
        self.world.config.max_level = 5
        self.world.config.leveling_curve = [0, 10, 30, 60, 100]
        self.world.config.save(
            update_fields=["starting_level", "max_level", "leveling_curve"]
        )

        player = self.create_player("Starter")
        player.initialize(include_starting_equipment=False)

        self.assertEqual(player.level, 3)
        self.assertEqual(player.experience, 30)

    def test_player_reset_uses_world_starting_level_by_default(self):
        self.world.config.starting_level = 2
        self.world.config.max_level = 5
        self.world.config.leveling_curve = [0, 10, 30, 60, 100]
        self.world.config.save(
            update_fields=["starting_level", "max_level", "leveling_curve"]
        )
        self.player.level = 5
        self.player.experience = 100
        self.player.save(update_fields=["level", "experience"])

        self.player.reset()
        self.player.refresh_from_db()

        self.assertEqual(self.player.level, 2)
        self.assertEqual(self.player.experience, 10)
