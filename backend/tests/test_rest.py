from unittest.mock import patch

from config import constants as adv_consts
from core.combat_formulas import normalize_combat_system
from core.computations import compute_stats
from spawns.models import CombatEncounter, Mob
from spawns.tasks import resolve_combat_encounter
from tests.base import WorldTestCase
from tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    create_active_effect,
    dispatch_text_command,
)


class TestRestCommands(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.stats = compute_stats(
            self.player.level,
            self.player.archetype,
            char=self.player,
        )
        self.player.health = self.stats["health_max"]
        self.player.energy = self.stats["energy_max"]
        self.player.stamina = self.stats["stamina_max"]
        self.player.in_game = True
        self.player.save(update_fields=["health", "energy", "stamina", "in_game"])
        self.spawn_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=["lifecycle"])
        self.world.config.combat_system = normalize_combat_system(
            {
                "variance": {
                    "enabled": False,
                    "percent": 0,
                },
                "profiles": {
                    "basic_physical": {
                        "power_scale": 1,
                        "use_weapon_damage": False,
                        "can_dodge": False,
                        "can_crit": False,
                        "mitigation": {
                            "armor": False,
                            "resilience": False,
                        },
                        "minimum": 0,
                    },
                },
            }
        )
        self.world.config.save(update_fields=["combat_system"])

    def _message_by_type(self, messages, message_type, player_key=None):
        for msg in messages:
            if player_key and msg["player_key"] != player_key:
                continue
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_rest_command_sets_player_state(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "rest")

        self.player.refresh_from_db()
        self.assertEqual(self.player.state, adv_consts.CHARACTER_STATE_RESTING)

        rest_message = self._message_by_type(messages, "cmd.rest.success", self.player.key)
        self.assertIsNotNone(rest_message)
        self.assertEqual(rest_message["text"], "You begin resting.")
        self.assertEqual(rest_message["data"]["actor"]["state"], "resting")

    def test_r_alias_defaults_to_rest_not_roll(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "r")

        self.player.refresh_from_db()
        self.assertEqual(self.player.state, adv_consts.CHARACTER_STATE_RESTING)
        self.assertIsNotNone(
            self._message_by_type(messages, "cmd.rest.success", self.player.key)
        )
        self.assertIsNone(self._message_by_type(messages, "cmd.roll.success", self.player.key))

    def test_stand_command_returns_player_to_standing(self):
        self.player.state = adv_consts.CHARACTER_STATE_RESTING
        self.player.save(update_fields=["state"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "stand")

        self.player.refresh_from_db()
        self.assertEqual(self.player.state, adv_consts.CHARACTER_STATE_STANDING)

        stand_message = self._message_by_type(messages, "cmd.stand.success", self.player.key)
        self.assertIsNotNone(stand_message)
        self.assertEqual(stand_message["text"], "You stand up.")
        self.assertEqual(stand_message["data"]["actor"]["state"], "standing")

    def test_rest_state_is_in_state_sync_payloads(self):
        from spawns.state_payloads import build_state_sync

        self.player.state = adv_consts.CHARACTER_STATE_RESTING
        self.player.save(update_fields=["state"])

        payload = build_state_sync(self.player).model_dump()

        self.assertEqual(payload["actor"]["state"], "resting")
        self.assertTrue(
            any(
                char["key"] == self.player.key and char["state"] == "resting"
                for char in payload["room"]["chars"]
            )
        )

    def test_rest_command_rejects_active_combat(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=100,
            health_max=100,
        )
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "rest")

        self.player.refresh_from_db()
        self.assertEqual(self.player.state, adv_consts.CHARACTER_STATE_STANDING)
        error = self._message_by_type(messages, "cmd.rest.error", self.player.key)
        self.assertIsNotNone(error)
        self.assertEqual(error["data"]["code"], "in_combat")

    def test_rest_command_rejects_hostile_effect_combat_tag(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=100,
            health_max=100,
        )
        create_active_effect(
            source=mob,
            target=self.player,
            payload={
                "effect": "dot",
                "category": "debuff",
                "label": "Burning Curse",
                "remaining_rounds": 2,
                "duration_rounds": 2,
                "tick": {"every_rounds": 1, "component": {"type": "damage"}},
            },
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "rest")

        self.player.refresh_from_db()
        self.assertEqual(self.player.state, adv_consts.CHARACTER_STATE_STANDING)
        error = self._message_by_type(messages, "cmd.rest.error", self.player.key)
        self.assertIsNotNone(error)
        self.assertEqual(error["data"]["code"], "in_combat")

    def test_entering_combat_moves_resting_player_to_standing(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self.player.state = adv_consts.CHARACTER_STATE_RESTING
        self.player.save(update_fields=["state"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] * 3,
            health_max=self.stats["attack_power"] * 3,
            attack_power=1,
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, "kill rat")

        self.player.refresh_from_db()
        self.assertEqual(self.player.state, adv_consts.CHARACTER_STATE_STANDING)
        engage_message = self._message_by_type(messages, "cmd.kill.success", self.player.key)
        self.assertIsNotNone(engage_message)
        self.assertEqual(engage_message["data"]["actor"]["state"], "combat")

    def test_getting_attacked_moves_resting_player_to_standing(self):
        self.player.state = adv_consts.CHARACTER_STATE_RESTING
        self.player.save(update_fields=["state"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] * 3,
            health_max=self.stats["attack_power"] * 3,
            attack_power=4,
            fights_back=True,
        )
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
        )

        with capture_game_messages() as messages:
            resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        self.assertEqual(self.player.state, adv_consts.CHARACTER_STATE_STANDING)
        attack_message = next(
            (
                msg["message"]
                for msg in messages
                if msg["player_key"] == self.player.key
                and msg["message"].get("type") == "notification.combat.attack"
                and msg["message"]["data"]["target"]["key"] == self.player.key
            ),
            None,
        )
        self.assertIsNotNone(attack_message)
        self.assertEqual(attack_message["data"]["target"]["state"], "combat")
