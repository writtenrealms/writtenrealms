import math
from copy import deepcopy
from unittest.mock import patch

from config import constants as api_consts
from core.computations import compute_stats
from spawns.models import CombatEncounter, Mob
from spawns.tasks import WR2_STANDING_REGEN_RATE, run_heartbeat_regen
from tests.base import WorldTestCase
from wr2_tests.utils import apply_basic_stat_system


class TestHeartbeatRegen(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.spawn_world.lifecycle = api_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=["lifecycle"])
        spawn_zone = self.spawn_world.zones.first()
        if spawn_zone and spawn_zone.rooms.exists():
            self.spawn_room = spawn_zone.rooms.first()
        else:
            self.spawn_room = self.room

    def test_player_regen_restores_health_energy_and_stamina(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)
        health_max = stats["health_max"]
        energy_max = stats["energy_max"]
        stamina_max = stats["stamina_max"]
        energy_base = stats["energy_base"]

        self.player.in_game = True
        self.player.health = max(health_max - 10, 0)
        self.player.energy = max(energy_max - 10, 0)
        self.player.stamina = max(stamina_max - 10, 0)
        self.player.save(update_fields=["in_game", "health", "energy", "stamina"])

        expected_health = min(
            health_max,
            self.player.health + math.ceil(health_max * WR2_STANDING_REGEN_RATE / 100),
        )
        expected_energy = min(
            energy_max,
            self.player.energy + math.ceil(energy_base * WR2_STANDING_REGEN_RATE / 100),
        )
        expected_stamina = min(
            stamina_max,
            self.player.stamina + WR2_STANDING_REGEN_RATE,
        )

        run_heartbeat_regen()

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, expected_health)
        self.assertEqual(self.player.energy, expected_energy)
        self.assertEqual(self.player.stamina, expected_stamina)

    def test_player_regen_skips_players_not_in_game(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)

        self.player.in_game = False
        self.player.health = max(stats["health_max"] - 10, 0)
        self.player.energy = max(stats["energy_max"] - 10, 0)
        self.player.stamina = max(stats["stamina_max"] - 10, 0)
        self.player.save(update_fields=["in_game", "health", "energy", "stamina"])

        before = (self.player.health, self.player.energy, self.player.stamina)
        run_heartbeat_regen()

        self.player.refresh_from_db()
        self.assertEqual((self.player.health, self.player.energy, self.player.stamina), before)

    def test_heartbeat_decrements_ability_cooldowns_outside_combat(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)

        self.player.in_game = True
        self.player.health = stats["health_max"]
        self.player.energy = stats["energy_max"]
        self.player.stamina = stats["stamina_max"]
        self.player.known_abilities = ["power-strike", "quick-jab"]
        self.player.ability_hotkeys = {"1": "power-strike", "2": "quick-jab"}
        self.player.ability_cooldowns = {"power-strike": 2, "quick-jab": 1}
        self.player.save(
            update_fields=[
                "in_game",
                "health",
                "energy",
                "stamina",
                "known_abilities",
                "ability_hotkeys",
                "ability_cooldowns",
            ]
        )

        with patch("spawns.tasks.publish_events") as publish_mock:
            result = run_heartbeat_regen()

        self.player.refresh_from_db()
        self.assertEqual(self.player.ability_cooldowns, {"power-strike": 1})
        self.assertEqual(result["players"], 0)
        self.assertEqual(result["ability_cooldowns"], 1)

        publish_mock.assert_called_once()
        events = publish_mock.call_args.args[0]
        self.assertEqual(publish_mock.call_args.kwargs["actor_key"], self.player.key)
        self.assertEqual(len(events), 1)
        message = events[0].to_message()
        self.assertEqual(events[0].recipients, [self.player.key])
        self.assertEqual(message["type"], "player.abilities.update")
        self.assertEqual(
            message["data"]["actor"]["ability_cooldowns"],
            {"power-strike": 1},
        )

    def test_heartbeat_leaves_active_combat_ability_cooldowns_to_combat_rounds(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)

        self.player.in_game = True
        self.player.health = stats["health_max"]
        self.player.energy = stats["energy_max"]
        self.player.stamina = stats["stamina_max"]
        self.player.ability_cooldowns = {"power-strike": 2}
        self.player.save(
            update_fields=[
                "in_game",
                "health",
                "energy",
                "stamina",
                "ability_cooldowns",
            ]
        )
        mob = Mob.objects.create(
            name="Sparring Mob",
            world=self.spawn_world,
            room=self.spawn_room,
            health=100,
            health_max=100,
        )
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.spawn_room,
            player=self.player,
            mob=mob,
        )

        with patch("spawns.tasks.publish_events") as publish_mock:
            result = run_heartbeat_regen()

        self.player.refresh_from_db()
        self.assertEqual(self.player.ability_cooldowns, {"power-strike": 2})
        self.assertEqual(result["players"], 0)
        self.assertEqual(result["ability_cooldowns"], 0)
        publish_mock.assert_not_called()

    def test_player_regen_in_combat_uses_explicit_health_energy_and_stamina_base(self):
        stat_system = deepcopy(self.world.config.stat_system)
        stat_system["formulas"].setdefault("base_stats", {}).update(
            {
                "health_regen": 1,
                "energy_regen": 2,
            }
        )
        self.world.config.stat_system = stat_system
        self.world.config.save(update_fields=["stat_system"])

        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)
        health_max = stats["health_max"]
        energy_max = stats["energy_max"]
        stamina_max = stats["stamina_max"]

        self.player.in_game = True
        self.player.health = max(health_max - 10, 0)
        self.player.energy = max(energy_max - 10, 0)
        self.player.stamina = max(stamina_max - 10, 0)
        self.player.save(update_fields=["in_game", "health", "energy", "stamina"])
        mob = Mob.objects.create(
            name="Sparring Mob",
            world=self.spawn_world,
            room=self.spawn_room,
            health=100,
            health_max=100,
        )
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.spawn_room,
            player=self.player,
            mob=mob,
        )

        run_heartbeat_regen()

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, max(health_max - 10, 0) + 1)
        self.assertEqual(self.player.energy, max(energy_max - 10, 0) + 2)
        self.assertEqual(
            self.player.stamina,
            max(stamina_max - 10, 0) + WR2_STANDING_REGEN_RATE,
        )

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
            energy=20,
            energy_max=40,
            energy_regen=2,
            stamina=20,
            stamina_max=30,
            stamina_regen=3,
            regen_rate=10,
        )

        run_heartbeat_regen()

        mob.refresh_from_db()
        self.assertEqual(mob.health, 113)
        self.assertEqual(mob.energy, 26)
        self.assertEqual(mob.stamina, 25)

    def test_mob_regen_in_combat_uses_explicit_health_energy_and_stamina_base(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

        mob = Mob.objects.create(
            name="A Mob",
            world=self.spawn_world,
            room=self.spawn_room,
            health=100,
            health_max=120,
            health_regen=1,
            energy=20,
            energy_max=40,
            energy_regen=2,
            stamina=20,
            stamina_max=30,
            stamina_regen=3,
            regen_rate=10,
        )
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.spawn_room,
            player=self.player,
            mob=mob,
        )

        run_heartbeat_regen()

        mob.refresh_from_db()
        self.assertEqual(mob.health, 101)
        self.assertEqual(mob.energy, 22)
        self.assertEqual(mob.stamina, 25)

    def test_regen_skips_non_running_worlds(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)

        self.player.in_game = True
        self.player.health = max(stats["health_max"] - 10, 0)
        self.player.energy = max(stats["energy_max"] - 10, 0)
        self.player.stamina = max(stats["stamina_max"] - 10, 0)
        self.player.save(update_fields=["in_game", "health", "energy", "stamina"])

        mob = Mob.objects.create(
            name="Dormant Mob",
            world=self.spawn_world,
            room=self.spawn_room,
            health=50,
            health_max=100,
            energy=5,
            energy_max=20,
            stamina=5,
            stamina_max=20,
            regen_rate=10,
        )

        self.spawn_world.lifecycle = api_consts.WORLD_LIFECYCLE_STOPPED
        self.spawn_world.save(update_fields=["lifecycle"])

        player_before = (self.player.health, self.player.energy, self.player.stamina)
        mob_before = (mob.health, mob.energy, mob.stamina)

        run_heartbeat_regen()

        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual((self.player.health, self.player.energy, self.player.stamina), player_before)
        self.assertEqual((mob.health, mob.energy, mob.stamina), mob_before)

    def test_player_regen_publishes_notification_event(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)
        health_max = stats["health_max"]
        energy_max = stats["energy_max"]
        stamina_max = stats["stamina_max"]

        self.player.in_game = True
        self.player.health = max(health_max - 10, 0)
        self.player.energy = max(energy_max - 10, 0)
        self.player.stamina = max(stamina_max - 10, 0)
        self.player.save(update_fields=["in_game", "health", "energy", "stamina"])

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
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)

        self.player.in_game = True
        self.player.health = stats["health_max"] + 1050
        self.player.energy = stats["energy_max"] + 1
        self.player.stamina = max(stats["stamina_max"] - 10, 0)
        self.player.save(update_fields=["in_game", "health", "energy", "stamina"])

        initial_health = self.player.health
        initial_energy = self.player.energy
        expected_stamina_max = max(stats["stamina_max"], self.player.stamina)
        expected_stamina = min(expected_stamina_max, self.player.stamina + WR2_STANDING_REGEN_RATE)

        with patch("spawns.tasks.publish_to_player") as publish_mock:
            run_heartbeat_regen()

        publish_mock.assert_called_once()
        _, message = publish_mock.call_args.args[:2]
        actor = message["data"]["actor"]
        self.assertEqual(actor["health"], initial_health)
        self.assertEqual(actor["health_max"], initial_health)
        self.assertEqual(actor["energy"], initial_energy)
        self.assertEqual(actor["energy_max"], initial_energy)
        self.assertEqual(actor["stamina"], expected_stamina)
        self.assertEqual(actor["stamina_max"], expected_stamina_max)
