import math
from unittest.mock import patch

from config import constants as api_consts
from core.computations import compute_stats
from spawns.models import Mob
from spawns.tasks import WR2_STANDING_REGEN_RATE, run_heartbeat_regen
from tests.base import WorldTestCase


class TestHeartbeatRegen(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.spawn_world.lifecycle = api_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=["lifecycle"])
        spawn_zone = self.spawn_world.zones.first()
        if spawn_zone and spawn_zone.rooms.exists():
            self.spawn_room = spawn_zone.rooms.first()
        else:
            self.spawn_room = self.room

    def test_player_regen_restores_health_mana_and_stamina(self):
        stats = compute_stats(self.player.level, self.player.archetype)
        health_max = stats["health_max"]
        mana_max = stats["mana_max"]
        stamina_max = stats["stamina_max"]
        mana_base = stats["mana_base"]

        self.player.in_game = True
        self.player.health = max(health_max - 10, 0)
        self.player.mana = max(mana_max - 10, 0)
        self.player.stamina = max(stamina_max - 10, 0)
        self.player.save(update_fields=["in_game", "health", "mana", "stamina"])

        expected_health = min(
            health_max,
            self.player.health + math.ceil(health_max * WR2_STANDING_REGEN_RATE / 100),
        )
        expected_mana = min(
            mana_max,
            self.player.mana + math.ceil(mana_base * WR2_STANDING_REGEN_RATE / 100),
        )
        expected_stamina = min(
            stamina_max,
            self.player.stamina + WR2_STANDING_REGEN_RATE,
        )

        run_heartbeat_regen()

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, expected_health)
        self.assertEqual(self.player.mana, expected_mana)
        self.assertEqual(self.player.stamina, expected_stamina)

    def test_player_regen_skips_players_not_in_game(self):
        stats = compute_stats(self.player.level, self.player.archetype)

        self.player.in_game = False
        self.player.health = max(stats["health_max"] - 10, 0)
        self.player.mana = max(stats["mana_max"] - 10, 0)
        self.player.stamina = max(stats["stamina_max"] - 10, 0)
        self.player.save(update_fields=["in_game", "health", "mana", "stamina"])

        before = (self.player.health, self.player.mana, self.player.stamina)
        run_heartbeat_regen()

        self.player.refresh_from_db()
        self.assertEqual((self.player.health, self.player.mana, self.player.stamina), before)

    def test_mob_regen_uses_mob_regen_attributes(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        mob = Mob.objects.create(
            name="A Mob",
            world=self.spawn_world,
            room=self.spawn_room,
            health=100,
            health_max=120,
            health_regen=1,
            mana=20,
            mana_max=40,
            mana_regen=2,
            stamina=20,
            stamina_max=30,
            stamina_regen=3,
            regen_rate=10,
        )

        run_heartbeat_regen()

        mob.refresh_from_db()
        self.assertEqual(mob.health, 113)
        self.assertEqual(mob.mana, 26)
        self.assertEqual(mob.stamina, 25)

    def test_regen_skips_non_running_worlds(self):
        stats = compute_stats(self.player.level, self.player.archetype)

        self.player.in_game = True
        self.player.health = max(stats["health_max"] - 10, 0)
        self.player.mana = max(stats["mana_max"] - 10, 0)
        self.player.stamina = max(stats["stamina_max"] - 10, 0)
        self.player.save(update_fields=["in_game", "health", "mana", "stamina"])

        mob = Mob.objects.create(
            name="Dormant Mob",
            world=self.spawn_world,
            room=self.spawn_room,
            health=50,
            health_max=100,
            mana=5,
            mana_max=20,
            stamina=5,
            stamina_max=20,
            regen_rate=10,
        )

        self.spawn_world.lifecycle = api_consts.WORLD_LIFECYCLE_STOPPED
        self.spawn_world.save(update_fields=["lifecycle"])

        player_before = (self.player.health, self.player.mana, self.player.stamina)
        mob_before = (mob.health, mob.mana, mob.stamina)

        run_heartbeat_regen()

        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual((self.player.health, self.player.mana, self.player.stamina), player_before)
        self.assertEqual((mob.health, mob.mana, mob.stamina), mob_before)

    def test_player_regen_publishes_notification_event(self):
        stats = compute_stats(self.player.level, self.player.archetype)
        health_max = stats["health_max"]
        mana_max = stats["mana_max"]
        stamina_max = stats["stamina_max"]

        self.player.in_game = True
        self.player.health = max(health_max - 10, 0)
        self.player.mana = max(mana_max - 10, 0)
        self.player.stamina = max(stamina_max - 10, 0)
        self.player.save(update_fields=["in_game", "health", "mana", "stamina"])

        expected_health = min(
            health_max,
            self.player.health + math.ceil(health_max * WR2_STANDING_REGEN_RATE / 100),
        )
        expected_stamina = min(stamina_max, self.player.stamina + WR2_STANDING_REGEN_RATE)

        with patch("spawns.tasks.publish_to_player") as publish_mock:
            run_heartbeat_regen()

        publish_mock.assert_called_once()
        player_key, message = publish_mock.call_args.args[:2]
        self.assertEqual(player_key, self.player.key)
        self.assertEqual(message["type"], "notification.regen")

        actor = message["data"]["actor"]
        self.assertEqual(actor["key"], self.player.key)
        self.assertEqual(actor["health"], expected_health)
        self.assertEqual(actor["health_max"], health_max)
        self.assertEqual(actor["stamina"], expected_stamina)
        self.assertEqual(actor["stamina_max"], stamina_max)

    def test_player_regen_notification_uses_current_vitals_as_floor_for_max_values(self):
        stats = compute_stats(self.player.level, self.player.archetype)

        self.player.in_game = True
        self.player.health = stats["health_max"] + 1050
        self.player.mana = stats["mana_max"] + 1
        self.player.stamina = max(stats["stamina_max"] - 10, 0)
        self.player.save(update_fields=["in_game", "health", "mana", "stamina"])

        initial_health = self.player.health
        initial_mana = self.player.mana
        expected_stamina_max = max(stats["stamina_max"], self.player.stamina)
        expected_stamina = min(expected_stamina_max, self.player.stamina + WR2_STANDING_REGEN_RATE)

        with patch("spawns.tasks.publish_to_player") as publish_mock:
            run_heartbeat_regen()

        publish_mock.assert_called_once()
        _, message = publish_mock.call_args.args[:2]
        actor = message["data"]["actor"]
        self.assertEqual(actor["health"], initial_health)
        self.assertEqual(actor["health_max"], initial_health)
        self.assertEqual(actor["mana"], initial_mana)
        self.assertEqual(actor["mana_max"], initial_mana)
        self.assertEqual(actor["stamina"], expected_stamina)
        self.assertEqual(actor["stamina_max"], expected_stamina_max)
