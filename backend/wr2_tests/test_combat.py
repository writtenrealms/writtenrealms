from unittest.mock import patch

from core.computations import compute_stats
from django.utils import timezone
from spawns.models import CombatEncounter, Mob
from spawns.tasks import resolve_combat_encounter
from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command


class TestKillCommand(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.stats = compute_stats(self.player.level, self.player.archetype)
        self.player.health = self.stats["health_max"]
        self.player.mana = self.stats["mana_max"]
        self.player.stamina = self.stats["stamina_max"]
        self.player.in_game = True
        self.player.save(update_fields=["health", "mana", "stamina", "in_game"])

    def _message_by_type(self, messages, message_type, player_key=None):
        for msg in messages:
            if player_key and msg["player_key"] != player_key:
                continue
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def _messages_by_type(self, messages, message_type, player_key=None):
        return [
            msg["message"]
            for msg in messages
            if msg["message"].get("type") == message_type
            and (player_key is None or msg["player_key"] == player_key)
        ]

    def test_kill_auto_resolves_until_mob_dies_and_awards_rewards(self):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] + 5,
            health_max=self.stats["attack_power"] + 5,
            attack_power=4,
            exp_worth=17,
            gold=300,
        )
        mob.create_corpse()
        exp_before = self.player.experience
        gold_before = self.player.gold
        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill rat")

        self.player.refresh_from_db()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(self.player.experience, exp_before + 17)
        self.assertEqual(self.player.gold, gold_before + 300)

        actor_attacks = self._messages_by_type(
            messages,
            "notification.combat.attack",
            self.player.key,
        )
        self.assertGreaterEqual(len(actor_attacks), 3)
        self.assertEqual(actor_attacks[0]["data"]["damage_taken"], self.stats["attack_power"])

        death_message = self._message_by_type(
            messages,
            "notification.death",
            self.player.key,
        )
        self.assertIsNotNone(death_message)
        self.assertEqual(death_message["text"], "Rat is dead! R.I.P.")
        self.assertEqual(death_message["data"]["actor"]["experience"], exp_before + 17)
        self.assertEqual(death_message["data"]["actor"]["gold"], gold_before + 300)
        self.assertEqual(death_message["data"]["gold_gained"], 300)
        self.assertTrue(
            any(
                item["type"] == "corpse" and "rat" in item["name"].lower()
                for item in death_message["data"]["room"]["inventory"]
            )
        )

        reward_message = self._message_by_type(
            messages,
            "notification.reward",
            self.player.key,
        )
        self.assertIsNotNone(reward_message)
        self.assertEqual(
            reward_message["text"],
            "You gain 17 experience.\nYou receive 300 gold.",
        )
        self.assertEqual(reward_message["data"]["actor"]["experience"], exp_before + 17)
        self.assertEqual(reward_message["data"]["actor"]["gold"], gold_before + 300)

        watcher_death = self._message_by_type(
            messages,
            "notification.death",
            watcher.key,
        )
        self.assertIsNotNone(watcher_death)
        self.assertEqual(watcher_death["text"], "Rat is dead! R.I.P.")
        self.assertIsNone(
            self._message_by_type(messages, "notification.reward", watcher.key)
        )

    def test_kill_can_get_player_killed_and_moves_them_to_death_room(self):
        graveyard = self.room.create_at("east")
        self.world.config.death_room = graveyard
        self.world.config.save(update_fields=["death_room"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Ogre",
            keywords="ogre",
            health=self.stats["attack_power"] * 3,
            health_max=self.stats["attack_power"] * 3,
            attack_power=self.stats["health_max"],
            exp_worth=99,
        )
        mob.create_corpse()

        watcher = self.create_player("Watcher", room=self.room)
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill ogre")

        self.player.refresh_from_db()
        self.assertTrue(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(self.player.room_id, graveyard.id)
        self.assertEqual(self.player.health, self.stats["health_max"])

        death_affect = self._message_by_type(messages, "affect.death", self.player.key)
        self.assertIsNotNone(death_affect)
        self.assertEqual(death_affect["data"]["room"]["id"], graveyard.id)

        watcher_death = self._message_by_type(messages, "notification.death", watcher.key)
        self.assertIsNotNone(watcher_death)
        self.assertEqual(watcher_death["data"]["deceased"]["key"], self.player.key)
        self.assertEqual(watcher_death["text"], "Ogre kills Joe.")

    def test_kill_with_positive_interval_starts_encounter_without_immediate_resolution(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] + 5,
            health_max=self.stats["attack_power"] + 5,
            attack_power=4,
            exp_worth=17,
        )
        mob.create_corpse()

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, "kill rat")

        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        mob.refresh_from_db()

        self.assertEqual(encounter.round_number, 0)
        self.assertEqual(mob.health, self.stats["attack_power"] + 5)
        self.assertIsNone(self._message_by_type(messages, "notification.combat.attack", self.player.key))
        engage_message = self._message_by_type(messages, "cmd.kill.success", self.player.key)
        self.assertIsNotNone(engage_message)
        self.assertEqual(engage_message["text"], "You engage Rat.")
        self.assertEqual(engage_message["data"]["actor"]["state"], "combat")
        self.assertEqual(engage_message["data"]["actor"]["target"]["key"], mob.key)
        schedule_mock.assert_called_once()
        self.assertEqual(
            schedule_mock.call_args.kwargs["kwargs"]["encounter_id"],
            encounter.id,
        )
        self.assertEqual(schedule_mock.call_args.kwargs["countdown"], 1.5)

    def test_scheduled_combat_round_advances_one_step_and_reschedules(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] * 3,
            health_max=self.stats["attack_power"] * 3,
            attack_power=4,
            fights_back=True,
            exp_worth=17,
        )
        mob.create_corpse()

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "kill rat")

        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        encounter.next_resolution_ts = timezone.now()
        encounter.save(update_fields=["next_resolution_ts"])

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as reschedule_mock:
            with self.captureOnCommitCallbacks(execute=True):
                with capture_game_messages() as messages:
                    resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        mob.refresh_from_db()
        self.player.refresh_from_db()

        self.assertEqual(encounter.round_number, 1)
        self.assertEqual(mob.health, self.stats["attack_power"] * 2)
        self.assertEqual(self.player.health, self.stats["health_max"] - 4)
        actor_attacks = self._messages_by_type(
            messages,
            "notification.combat.attack",
            self.player.key,
        )
        self.assertEqual(len(actor_attacks), 2)
        self.assertEqual(actor_attacks[0]["data"]["actor"]["state"], "combat")
        self.assertEqual(actor_attacks[1]["data"]["target"]["key"], self.player.key)
        reschedule_mock.assert_called_once()
        self.assertEqual(
            reschedule_mock.call_args.kwargs["kwargs"]["encounter_id"],
            encounter.id,
        )
        self.assertEqual(reschedule_mock.call_args.kwargs["countdown"], 1.5)

    def test_manual_combat_interval_advances_one_round_per_kill_command(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=["combat_resolution_interval"])

        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Rat",
            keywords="rat",
            health=self.stats["attack_power"] * 2,
            health_max=self.stats["attack_power"] * 2,
            attack_power=4,
            fights_back=True,
            exp_worth=9,
            gold=25,
        )
        mob.create_corpse()
        exp_before = self.player.experience
        gold_before = self.player.gold

        with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
            with capture_game_messages() as first_messages:
                dispatch_text_command(self.player.id, "kill rat")

            encounter = CombatEncounter.objects.get(
                player=self.player,
                mob=mob,
                status=CombatEncounter.STATUS_ACTIVE,
            )
            mob.refresh_from_db()
            self.player.refresh_from_db()

            self.assertEqual(encounter.round_number, 1)
            self.assertEqual(mob.health, self.stats["attack_power"])
            self.assertEqual(self.player.health, self.stats["health_max"] - 4)
            self.assertIsNotNone(self._message_by_type(first_messages, "cmd.kill.success", self.player.key))

            with capture_game_messages() as second_messages:
                dispatch_text_command(self.player.id, "kill rat")

        self.player.refresh_from_db()
        schedule_mock.assert_not_called()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(
            CombatEncounter.objects.get(pk=encounter.id).status,
            CombatEncounter.STATUS_FINISHED,
        )
        self.assertEqual(self.player.experience, exp_before + 9)
        self.assertEqual(self.player.gold, gold_before + 25)
        death_message = self._message_by_type(
            second_messages,
            "notification.death",
            self.player.key,
        )
        self.assertIsNotNone(death_message)
        self.assertEqual(death_message["text"], "Rat is dead! R.I.P.")
        reward_message = self._message_by_type(
            second_messages,
            "notification.reward",
            self.player.key,
        )
        self.assertIsNotNone(reward_message)
        self.assertEqual(
            reward_message["text"],
            "You gain 9 experience.\nYou receive 25 gold.",
        )
