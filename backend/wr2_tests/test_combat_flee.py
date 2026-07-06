from unittest.mock import patch

from config import constants as adv_consts
from core.combat_formulas import normalize_combat_system
from core.computations import compute_stats
from django.utils import timezone
from spawns.actions.movement_costs import movement_cost
from spawns.models import CombatEncounter, Mob
from spawns.tasks import resolve_combat_encounter
from tests.base import WorldTestCase
from wr2_tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)


class TestCombatFlee(WorldTestCase):
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
        self.world.config.combat_system = normalize_combat_system({
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
        })
        self.world.config.save(update_fields=["combat_system"])
        self.escape_room = self.room.create_at("east")
        self.escape_room.type = adv_consts.ROOM_TYPE_FOREST
        self.escape_room.save(update_fields=["type"])

    def _mob(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a rat",
            keywords="rat",
            health=self.stats["attack_power"] * 10,
            health_max=self.stats["attack_power"] * 10,
            attack_power=4,
            fights_back=True,
        )
        mob.create_corpse()
        return mob

    def _messages_by_type(self, messages, message_type):
        return [
            msg["message"]
            for msg in messages
            if msg["player_key"] == self.player.key
            and msg["message"].get("type") == message_type
        ]

    def test_scheduled_flee_skips_one_player_round_then_exits_before_damage(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        mob = self._mob()

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "kill rat")
            with capture_game_messages() as flee_messages:
                dispatch_text_command(self.player.id, "flee")

        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(encounter.pending_flee["status"], "preparing")
        self.assertEqual(
            encounter.pending_flee["movement_cost"],
            movement_cost(self.escape_room),
        )
        self.player.refresh_from_db()
        self.assertEqual(
            self.player.stamina,
            self.stats["stamina_max"] - movement_cost(self.escape_room),
        )
        self.assertEqual(
            self._messages_by_type(flee_messages, "cmd.flee.success")[0]["text"],
            "You prepare to flee.",
        )

        encounter.next_resolution_ts = timezone.now()
        encounter.save(update_fields=["next_resolution_ts"])
        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as first_round_messages:
                resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        mob.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(encounter.round_number, 1)
        self.assertEqual(encounter.pending_flee["status"], "ready")
        self.assertEqual(mob.health, self.stats["attack_power"] * 10)
        self.assertEqual(self.player.health, self.stats["health_max"] - 4)
        self.assertEqual(self.player.room_id, self.room.id)
        flee_round_message = self._messages_by_type(
            first_round_messages,
            "notification.combat.flee",
        )[0]
        self.assertEqual(flee_round_message["text"], "You look for an opening to flee.")
        self.assertEqual(
            flee_round_message["data"]["round_id"],
            f"encounter:{encounter.id}:1",
        )
        mob_attack = self._messages_by_type(
            first_round_messages,
            "notification.combat.attack",
        )[0]
        self.assertEqual(mob_attack["text"], "A rat hits you for 4 damage.")
        self.assertEqual(
            mob_attack["data"]["round_id"],
            flee_round_message["data"]["round_id"],
        )

        encounter.next_resolution_ts = timezone.now()
        encounter.save(update_fields=["next_resolution_ts"])
        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as second_round_messages:
                resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        mob.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(self.player.room_id, self.escape_room.id)
        self.assertEqual(self.player.health, self.stats["health_max"] - 4)
        self.assertEqual(mob.health, self.stats["attack_power"] * 10)
        flee_message = self._messages_by_type(second_round_messages, "cmd.flee.success")[0]
        self.assertEqual(flee_message["text"], "You flee east.")
        self.assertEqual(flee_message["data"]["round_id"], f"encounter:{encounter.id}:2")

    def test_manual_flee_advances_prepare_round_then_completes_on_next_round_command(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        mob = self._mob()

        dispatch_text_command(self.player.id, "kill rat")
        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.player.health = self.stats["health_max"]
        self.player.save(update_fields=["health"])

        with capture_game_messages() as first_messages:
            dispatch_text_command(self.player.id, "flee")

        encounter.refresh_from_db()
        mob.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(encounter.round_number, 2)
        self.assertEqual(encounter.pending_flee["status"], "ready")
        self.assertEqual(mob.health, self.stats["attack_power"] * 9)
        self.assertEqual(self.player.health, self.stats["health_max"] - 4)
        self.assertEqual(
            self.player.stamina,
            self.stats["stamina_max"] - movement_cost(self.escape_room),
        )
        flee_round_message = self._messages_by_type(
            first_messages,
            "notification.combat.flee",
        )[0]
        self.assertEqual(flee_round_message["text"], "You look for an opening to flee.")
        self.assertEqual(
            flee_round_message["data"]["round_id"],
            f"encounter:{encounter.id}:2",
        )
        mob_attack = self._messages_by_type(
            first_messages,
            "notification.combat.attack",
        )[0]
        self.assertEqual(mob_attack["text"], "A rat hits you for 4 damage.")
        self.assertEqual(
            mob_attack["data"]["round_id"],
            flee_round_message["data"]["round_id"],
        )

        with capture_game_messages() as second_messages:
            dispatch_text_command(self.player.id, "flee")

        encounter.refresh_from_db()
        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(self.player.room_id, self.escape_room.id)
        self.assertEqual(self.player.health, self.stats["health_max"] - 4)
        self.assertEqual(mob.health, self.stats["attack_power"] * 9)
        flee_message = self._messages_by_type(second_messages, "cmd.flee.success")[0]
        self.assertEqual(flee_message["text"], "You flee east.")
        self.assertEqual(flee_message["data"]["round_id"], f"encounter:{encounter.id}:3")

    def test_flee_finishes_all_active_origin_room_encounters(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self.escape_room.type = adv_consts.ROOM_TYPE_ROAD
        self.escape_room.save(update_fields=["type"])
        sparabara = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a sparabara",
            keywords="sparabara",
            health=self.stats["attack_power"] * 10,
            health_max=self.stats["attack_power"] * 10,
            target_priority=10,
            fights_back=False,
        )
        archer = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a persian archer",
            keywords="archer",
            health=self.stats["attack_power"] * 10,
            health_max=self.stats["attack_power"] * 10,
            fights_back=False,
        )
        primary_encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=sparabara,
            status=CombatEncounter.STATUS_ACTIVE,
            resolution_interval=-1,
        )
        secondary_encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=archer,
            status=CombatEncounter.STATUS_ACTIVE,
            resolution_interval=-1,
        )

        dispatch_text_command(self.player.id, "flee")
        dispatch_text_command(self.player.id, "flee")

        primary_encounter.refresh_from_db()
        secondary_encounter.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(primary_encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(secondary_encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(self.player.room_id, self.escape_room.id)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "scan west")

        scan_messages = self._messages_by_type(messages, "cmd.scan.success")
        self.assertTrue(scan_messages, messages)
        scan_message = scan_messages[0]
        self.assertIn("A sparabara is here.", scan_message["text"])
        self.assertIn("A persian archer is here.", scan_message["text"])
        self.assertNotIn("fighting", scan_message["text"])
        self.assertTrue(
            all(char.get("target") is None for char in scan_message["data"]["chars"])
        )

    def test_flee_requires_active_combat_and_an_exit(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "flee")

        error = self._messages_by_type(messages, "cmd.flee.error")[0]
        self.assertEqual(error["data"]["code"], "not_in_combat")

        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self.room.east = None
        self.room.save(update_fields=["east"])
        self._mob()
        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "kill rat")

        with capture_game_messages() as no_exit_messages:
            dispatch_text_command(self.player.id, "flee")

        error = self._messages_by_type(no_exit_messages, "cmd.flee.error")[0]
        self.assertEqual(error["data"]["code"], "no_flee_exit")

    def test_flee_requires_enough_stamina_for_destination_room(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        flee_cost = movement_cost(self.escape_room)
        self.player.stamina = flee_cost - 1
        self.player.save(update_fields=["stamina"])
        mob = self._mob()
        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "kill rat")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "flee")

        error = self._messages_by_type(messages, "cmd.flee.error")[0]
        self.assertEqual(error["data"]["code"], "exhausted")
        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(encounter.pending_flee, {})
        self.player.refresh_from_db()
        self.assertEqual(self.player.stamina, flee_cost - 1)
