from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from config import constants as api_consts
from config import game_settings as adv_config
from core.computations import compute_stats
from spawns.models import Player
from spawns.tasks import WR2_STANDING_REGEN_RATE, run_game_heartbeat
from worlds.models import World


User = get_user_model()


class TestBlankWorldDefaults(TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("blank@example.com", "p")

    def _new_spawn_world(self):
        world = World.objects.new_world(name="Blank World", author=self.user)
        return world, world.create_spawn_world()

    def test_new_blank_world_has_default_stamina_stats(self):
        world, spawn_world = self._new_spawn_world()

        stat_system = world.config.stat_system
        self.assertEqual(
            stat_system["formulas"]["base_resources"]["stamina"]["flat"],
            adv_config.PLAYER_STARTING_MAX_STAMINA,
        )
        self.assertEqual(
            stat_system["formulas"]["base_stats"]["stamina_regen"],
            adv_config.PLAYER_STARTING_STAMINA_REGEN,
        )

        stats = compute_stats(1, "warrior", world=spawn_world)
        self.assertEqual(stats["stamina_max"], adv_config.PLAYER_STARTING_MAX_STAMINA)
        self.assertEqual(stats["stamina_regen"], adv_config.PLAYER_STARTING_STAMINA_REGEN)

    def test_new_blank_world_character_starts_with_stamina_and_regenerates(self):
        world, spawn_world = self._new_spawn_world()
        spawn_world.lifecycle = api_consts.WORLD_LIFECYCLE_RUNNING
        spawn_world.save(update_fields=["lifecycle"])

        player = Player.objects.create(
            name="Runner",
            user=self.user,
            world=spawn_world,
            room=world.config.starting_room,
        ).initialize(starting_eq=False)

        self.assertEqual(player.stamina, adv_config.PLAYER_STARTING_MAX_STAMINA)

        player.in_game = True
        player.stamina = adv_config.PLAYER_STARTING_MAX_STAMINA - 10
        player.save(update_fields=["in_game", "stamina"])

        with patch("spawns.tasks.publish_to_player"):
            result = run_game_heartbeat()

        player.refresh_from_db()
        self.assertEqual(result["players"], 1)
        self.assertEqual(
            player.stamina,
            adv_config.PLAYER_STARTING_MAX_STAMINA - 10 + WR2_STANDING_REGEN_RATE,
        )
