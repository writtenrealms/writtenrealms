from tests.combat_fixtures import create_combat_encounter, combat_member, save_combat_fixture, refresh_combat_fixture, dispatch_and_drain_combat
import math
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from builders.models import AbilityDefinition
from config import constants as api_consts
from core.computations import compute_stats
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from spawns.actions.combat import resolve_due_character_effects
from spawns.models import ActiveEffect, CombatEncounter, Mob
from spawns.tasks import (
    WR2_RESTING_REGEN_MULTIPLIER,
    WR2_STANDING_REGEN_RATE,
    run_game_heartbeat,
)
from tests.base import WorldTestCase
from tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    create_active_effect,
    dispatch_text_command,
    dispatch_text_command_as_mob,
    replace_active_effects,
)


class TestGameHeartbeat(WorldTestCase):
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

    def _cast_crest(self, cast_at, *, periodic=False):
        component = {
            "type": "effect",
            "effect": "crest",
            "category": "buff",
            "scope": "character",
            "target": "self",
            "duration": {"rounds": 3},
            "primitives": [{"type": "damage_absorb", "amount": 30}],
        }
        if periodic:
            component["tick"] = {
                "every_rounds": 1,
                "primitives": [{
                    "type": "resource_change",
                    "resource": "health",
                    "amount": 1,
                    "target": "effect.target",
                }],
            }
        AbilityDefinition.objects.update_or_create(
            world=self.world,
            slug="crest",
            defaults={
                "name": "Crest",
                "command_verbs": ["crest"],
                "target": {"type": "self", "default": "self", "allow_out_of_combat": True},
                "cooldown": {"rounds": 3},
                "consumes_primary_action_on_resolve": False,
                "components": [component],
            },
        )
        self.player.in_game = True
        self.player.known_abilities = ["crest"]
        self.player.ability_cooldowns = {}
        self.player.save(update_fields=["in_game", "known_abilities", "ability_cooldowns"])
        with patch("django.utils.timezone.now", return_value=cast_at):
            dispatch_text_command(self.player.id, "crest")
        self.player.refresh_from_db()
        self.assertEqual(self.player.ability_cooldowns, {"crest": 3})
        self.assertEqual(self.player.active_effects[0]["remaining_rounds"], 3)

    def test_equal_cooldown_and_effect_tick_together_on_every_heartbeat(self):
        start_at = timezone.now()
        for index, (interval, periodic) in enumerate(((2, False), (5, False), (-1, True))):
            cast_at = start_at + timedelta(seconds=20 * index)
            with self.subTest(combat_interval=interval, periodic=periodic):
                self.world.config.combat_resolution_interval = interval
                self.world.config.save(update_fields=["combat_resolution_interval"])
                self._cast_crest(cast_at, periodic=periodic)
                # Cast just before a heartbeat, then introduce both polling
                # jitter and a delayed heartbeat. Each pulse remains one round.
                for offset, remaining in ((0.1, 2), (2.05, 1), (10, 0)):
                    pulse_at = cast_at + timedelta(seconds=offset)

                    def process_effects(**kwargs):
                        clock_mock.return_value = pulse_at + timedelta(milliseconds=50)
                        return resolve_due_character_effects(due_at=pulse_at, **kwargs)

                    with (
                        patch("django.utils.timezone.now", return_value=pulse_at) as clock_mock,
                        patch("spawns.actions.combat.resolve_due_character_effects", side_effect=process_effects),
                        patch("spawns.tasks.publish_events") as publish_mock,
                    ):
                        result = run_game_heartbeat()

                    self.player.refresh_from_db()
                    self.assertEqual(self.player.ability_cooldowns.get("crest", 0), remaining)
                    effects = self.player.active_effects
                    self.assertEqual(effects[0]["remaining_rounds"] if effects else 0, remaining)
                    self.assertEqual(result["ability_cooldowns"], 1)
                    self.assertEqual(result["active_effects"], 1)
                    updates = [
                        event.data["actor"]
                        for call in publish_mock.call_args_list
                        for event in call.args[0]
                        if event.type == "player.abilities.update"
                    ]
                    # No intermediate cooldown-only snapshot with a stale buff.
                    self.assertEqual(len(updates), 1)
                    self.assertEqual(updates[0]["ability_cooldowns"].get("crest", 0), remaining)
                    self.assertEqual(
                        updates[0]["active_effects"][0]["remaining_rounds"] if remaining else 0,
                        remaining,
                    )

    def test_replaying_effect_pulse_does_not_tick_effect_or_cooldown_twice(self):
        cast_at = timezone.now()
        self._cast_crest(cast_at)
        pulse_at = cast_at + timedelta(seconds=1)
        kwargs = {"due_at": pulse_at, "cooldown_player_ids": {self.player.id}}
        resolve_due_character_effects(**kwargs)
        self.assertEqual(resolve_due_character_effects(**kwargs), [])
        self.player.refresh_from_db()
        self.assertEqual(self.player.ability_cooldowns, {"crest": 2})
        self.assertEqual(self.player.active_effects[0]["remaining_rounds"], 2)

    def test_effect_batch_limit_defers_cooldown_with_effect_and_serves_oldest_next(self):
        cast_at = timezone.now()
        self._cast_crest(cast_at)
        other = self.create_player("Other")
        other.in_game = True
        other.ability_cooldowns = {"crest": 3}
        other.save(update_fields=["in_game", "ability_cooldowns"])
        with patch("django.utils.timezone.now", return_value=cast_at + timedelta(milliseconds=1)):
            create_active_effect(target=other, source=other, payload={
                "effect": "crest", "remaining_rounds": 3,
            })

        def one_target(**kwargs):
            return resolve_due_character_effects(limit=1, **kwargs)

        for offset, expected in ((1, (2, 3)), (3, (2, 2))):
            with (
                patch("django.utils.timezone.now", return_value=cast_at + timedelta(seconds=offset)),
                patch("spawns.actions.combat.resolve_due_character_effects", side_effect=one_target),
            ):
                run_game_heartbeat()
            for player, remaining in zip((self.player, other), expected):
                player.refresh_from_db()
                self.assertEqual(player.ability_cooldowns, {"crest": remaining})
                self.assertEqual(player.active_effects[0]["remaining_rounds"], remaining)

    def test_combined_cooldown_adds_only_one_write_per_effect_target(self):
        cast_at = timezone.now()
        self._cast_crest(cast_at)
        for index in range(5):
            create_active_effect(target=self.player, source=self.player, payload={
                "effect": f"buff-{index}", "remaining_rounds": 3,
            })
        pulse_at = cast_at + timedelta(seconds=2)
        with CaptureQueriesContext(connection) as effect_queries:
            resolve_due_character_effects(due_at=pulse_at)
        with CaptureQueriesContext(connection) as combined_queries:
            events = resolve_due_character_effects(
                due_at=pulse_at + timedelta(seconds=2),
                cooldown_player_ids={self.player.id},
            )
        # Player.save writes the cooldown and its existing post-save hook reads
        # player configuration once. Neither cost grows with the six effects.
        self.assertLessEqual(len(combined_queries), len(effect_queries) + 2)
        cooldown_writes = [
            query for query in combined_queries
            if query["sql"].startswith('UPDATE "spawns_player"')
            and '"ability_cooldowns"' in query["sql"]
        ]
        self.assertEqual(len(cooldown_writes), 1)
        self.assertEqual(len([event for event in events if event.type == "player.abilities.update"]), 1)

    def test_combined_cooldown_rolls_back_with_failed_effect_outbox_insert(self):
        cast_at = timezone.now()
        self._cast_crest(cast_at)
        with (
            patch("spawns.actions.combat.enqueue_game_events", side_effect=RuntimeError("insert failed")),
            self.assertRaises(RuntimeError),
        ):
            resolve_due_character_effects(
                due_at=cast_at + timedelta(seconds=1),
                cooldown_player_ids={self.player.id},
                persist_events=True,
            )
        self.player.refresh_from_db()
        self.assertEqual(self.player.ability_cooldowns, {"crest": 3})
        self.assertEqual(self.player.active_effects[0]["remaining_rounds"], 3)

    def test_active_combat_effects_do_not_use_detached_effect_batch_capacity(self):
        cast_at = timezone.now()
        self._cast_crest(cast_at)
        mob = self.create_mob("Opponent", health=100, health_max=100)
        create_combat_encounter(
            world=self.spawn_world, room=self.room, player=self.player, mob=mob,
        )
        other = self.create_player("Outside combat")
        other.in_game = True
        other.save(update_fields=["in_game"])
        create_active_effect(target=other, source=other, payload={
            "effect": "crest", "remaining_rounds": 3,
        })
        resolve_due_character_effects(due_at=cast_at + timedelta(seconds=2), limit=1)
        self.assertEqual(other.active_effects[0]["remaining_rounds"], 2)
        self.assertEqual(self.player.active_effects[0]["remaining_rounds"], 3)

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

        run_game_heartbeat()

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, expected_health)
        self.assertEqual(self.player.energy, expected_energy)
        self.assertEqual(self.player.stamina, expected_stamina)

    def test_resting_player_regen_triples_standing_base_rate(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)
        health_max = stats["health_max"]
        energy_max = stats["energy_max"]
        stamina_max = stats["stamina_max"]
        energy_base = stats["energy_base"]
        resting_regen_rate = WR2_STANDING_REGEN_RATE * WR2_RESTING_REGEN_MULTIPLIER

        self.player.in_game = True
        self.player.state = api_consts.CHARACTER_STATE_RESTING
        self.player.health = max(health_max - 20, 0)
        self.player.energy = max(energy_max - 20, 0)
        self.player.stamina = max(stamina_max - 20, 0)
        self.player.save(update_fields=["in_game", "state", "health", "energy", "stamina"])

        expected_health = min(
            health_max,
            self.player.health + math.ceil(health_max * resting_regen_rate / 100),
        )
        expected_energy = min(
            energy_max,
            self.player.energy + math.ceil(energy_base * resting_regen_rate / 100),
        )
        expected_stamina = min(
            stamina_max,
            self.player.stamina + resting_regen_rate,
        )

        run_game_heartbeat()

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
        run_game_heartbeat()

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
            result = run_game_heartbeat()

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

    def test_heartbeat_decrements_active_effects_outside_combat(self):
        self.player.in_game = True
        replace_active_effects(target=self.player, source=self.player, payloads=[
            {
                "effect": "shout",
                "category": "buff",
                "scope": "character",
                "source": {"type": "player", "id": self.player.id},
                "target": {"type": "player", "id": self.player.id},
                "remaining_rounds": 2,
                "duration_rounds": 2,
                "rounds_elapsed": 0,
                "label": "Shout",
                "stack_key": "shout-damage-output",
                "stacking": "refresh",
                "primitives": [
                    {
                        "type": "combat_modifier",
                        "phase": "outgoing_damage",
                        "multiplier": 1.2,
                    }
                ],
            }
        ])
        self.player.save(update_fields=["in_game"])

        with patch("spawns.tasks.publish_events") as publish_mock:
            result = run_game_heartbeat()

        self.player.refresh_from_db()
        self.assertEqual(self.player.active_effects[0]["remaining_rounds"], 1)
        self.assertEqual(result["active_effects"], 1)
        publish_mock.assert_called_once()

        self.player.active_effect_records.update(next_tick_ts=timezone.now())
        with patch("spawns.tasks.publish_events"):
            run_game_heartbeat()

        self.player.refresh_from_db()
        self.assertEqual(self.player.active_effects, [])

    def test_hostile_periodic_effect_keeps_actor_combat_tagged_for_regen(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)
        self.player.in_game = True
        self.player.health = stats["health_max"] - 10
        self.player.save(update_fields=["in_game", "health"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.spawn_room,
            name="Hexer",
            health=20,
            health_max=20,
            attack_power=1,
            fights_back=False,
        )
        effect = create_active_effect(
            target=self.player,
            source=mob,
            payload={
                "effect": "dot",
                "category": "debuff",
                "remaining_rounds": 2,
                "tick": {
                    "every_rounds": 1,
                    "component": {
                        "type": "damage",
                        "profile": "basic_physical",
                        "overrides": {"multiplier": 1},
                    },
                },
            },
        )
        effect.next_tick_ts = timezone.now() + timedelta(minutes=1)
        effect.save(update_fields=["next_tick_ts"])

        run_game_heartbeat()

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, stats["health_max"] - 10)

        effect.delete()
        run_game_heartbeat()
        self.player.refresh_from_db()
        self.assertGreater(self.player.health, stats["health_max"] - 10)

    def test_offline_periodic_target_pauses_without_tagging_source(self):
        observer = self.create_player("Observer")
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        self.player.in_game = False
        self.player.health = 20
        self.player.save(update_fields=["in_game", "health"])
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.spawn_room,
            name="Hexer",
            health=10,
            health_max=20,
            regen_rate=10,
            fights_back=False,
        )
        effect = create_active_effect(
            target=self.player,
            source=mob,
            payload={
                "effect": "dot",
                "category": "debuff",
                "remaining_rounds": 2,
                "tick": {
                    "every_rounds": 1,
                    "component": {"type": "damage", "profile": "basic_physical"},
                },
            },
        )
        effect.next_tick_ts = timezone.now() - timedelta(seconds=1)
        effect.save(update_fields=["next_tick_ts"])

        run_game_heartbeat()

        self.player.refresh_from_db()
        mob.refresh_from_db()
        effect.refresh_from_db()
        self.assertEqual(self.player.health, 20)
        self.assertEqual(effect.remaining_rounds, 2)
        self.assertGreater(mob.health, 10)

    def test_due_effect_limit_processes_oldest_target_first(self):
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        older_target = self.create_player("Older")
        older_target.in_game = True
        older_target.save(update_fields=["in_game"])
        newer = create_active_effect(
            target=self.player,
            source=self.player,
            payload={
                "effect": "shout",
                "remaining_rounds": 1,
                "duration_rounds": 1,
            },
        )
        older = create_active_effect(
            target=older_target,
            source=older_target,
            payload={
                "effect": "shout",
                "remaining_rounds": 1,
                "duration_rounds": 1,
            },
        )
        now = timezone.now()
        newer.next_tick_ts = now - timedelta(seconds=5)
        newer.save(update_fields=["next_tick_ts"])
        older.next_tick_ts = now - timedelta(seconds=10)
        older.save(update_fields=["next_tick_ts"])

        resolve_due_character_effects(due_at=now, limit=1)

        self.assertFalse(ActiveEffect.objects.filter(pk=older.id).exists())
        self.assertTrue(ActiveEffect.objects.filter(pk=newer.id).exists())

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
        create_combat_encounter(
            world=self.spawn_world,
            room=self.spawn_room,
            player=self.player,
            mob=mob,
        )

        with patch("spawns.tasks.publish_events") as publish_mock:
            result = run_game_heartbeat()

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
        create_combat_encounter(
            world=self.spawn_world,
            room=self.spawn_room,
            player=self.player,
            mob=mob,
        )

        run_game_heartbeat()

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
        run_game_heartbeat()

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
        create_combat_encounter(
            world=self.spawn_world,
            room=self.spawn_room,
            player=self.player,
            mob=mob,
        )

        run_game_heartbeat()

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
        effect = create_active_effect(
            target=mob,
            source=self.player,
            payload={
                "effect": "dot",
                "category": "debuff",
                "remaining_rounds": 2,
                "tick": {
                    "every_rounds": 1,
                    "component": {"type": "damage", "profile": "basic_physical"},
                },
            },
        )
        effect.next_tick_ts = timezone.now() - timedelta(seconds=1)
        effect.save(update_fields=["next_tick_ts"])

        self.spawn_world.lifecycle = api_consts.WORLD_LIFECYCLE_STOPPED
        self.spawn_world.save(update_fields=["lifecycle"])

        player_before = (self.player.health, self.player.energy, self.player.stamina)
        mob_before = (mob.health, mob.energy, mob.stamina)

        run_game_heartbeat()

        self.player.refresh_from_db()
        mob.refresh_from_db()
        effect.refresh_from_db()
        self.assertEqual((self.player.health, self.player.energy, self.player.stamina), player_before)
        self.assertEqual((mob.health, mob.energy, mob.stamina), mob_before)
        self.assertEqual(effect.remaining_rounds, 2)

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
            run_game_heartbeat()

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
            run_game_heartbeat()

        publish_mock.assert_called_once()
        _, message = publish_mock.call_args.args[:2]
        actor = message["data"]["actor"]
        self.assertEqual(actor["health"], initial_health)
        self.assertEqual(actor["health_max"], initial_health)
        self.assertEqual(actor["energy"], initial_energy)
        self.assertEqual(actor["energy_max"], initial_energy)
        self.assertEqual(actor["stamina"], expected_stamina)
        self.assertEqual(actor["stamina_max"], expected_stamina_max)


class TestRegenCommand(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.player.is_builder = True
        self.player.save(update_fields=["is_builder"])

    def _message_by_type(self, messages, message_type, player_key=None):
        for msg in messages:
            if player_key and msg["player_key"] != player_key:
                continue
            if msg["message"].get("type") == message_type:
                return msg["message"]
        return None

    def test_builder_regen_without_args_restores_self_resources(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)
        self.player.health = max(stats["health_max"] - 10, 0)
        self.player.energy = max(stats["energy_max"] - 10, 0)
        self.player.stamina = max(stats["stamina_max"] - 10, 0)
        self.player.save(update_fields=["health", "energy", "stamina"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/regen")

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, stats["health_max"])
        self.assertEqual(self.player.energy, stats["energy_max"])
        self.assertEqual(self.player.stamina, stats["stamina_max"])
        message = self._message_by_type(messages, "cmd./regen.success", self.player.key)
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], self.player.key)
        self.assertEqual(message["data"]["resources"], ["health", "energy", "stamina"])

    def test_builder_regen_target_resource_only_restores_that_resource(self):
        target = self.create_player("Target", room=self.room)
        stats = compute_stats(target.level, target.archetype, char=target)
        target.health = max(stats["health_max"] - 10, 0)
        target.energy = max(stats["energy_max"] - 10, 0)
        target.stamina = max(stats["stamina_max"] - 10, 0)
        target.save(update_fields=["health", "energy", "stamina"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, f"/regen {target.key} energy")

        target.refresh_from_db()
        self.assertEqual(target.health, max(stats["health_max"] - 10, 0))
        self.assertEqual(target.energy, stats["energy_max"])
        self.assertEqual(target.stamina, max(stats["stamina_max"] - 10, 0))
        message = self._message_by_type(messages, "cmd./regen.success", self.player.key)
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["resource"], "energy")
        self.assertEqual(message["data"]["target"]["key"], target.key)
        notification = self._message_by_type(messages, "notification.regen", target.key)
        self.assertIsNotNone(notification)
        self.assertEqual(notification["data"]["actor"]["key"], target.key)
        self.assertEqual(notification["data"]["actor"]["energy"], stats["energy_max"])

    def test_mob_regen_without_args_restores_self_resources(self):
        mob = Mob.objects.create(
            name="A Mob",
            keywords="mob",
            world=self.spawn_world,
            room=self.room,
            health=1,
            health_max=30,
            energy=2,
            energy_max=20,
            stamina=3,
            stamina_max=10,
        )

        with capture_game_messages() as messages:
            dispatch_text_command_as_mob(mob.id, "/regen")

        mob.refresh_from_db()
        self.assertEqual(mob.health, 30)
        self.assertEqual(mob.energy, 20)
        self.assertEqual(mob.stamina, 10)
        message = self._message_by_type(messages, "cmd./regen.success", mob.key)
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["target"]["key"], mob.key)

    def test_mob_regen_can_restore_target_player_resource(self):
        mob = Mob.objects.create(
            name="A Healer",
            keywords="healer",
            world=self.spawn_world,
            room=self.room,
        )
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)
        self.player.health = max(stats["health_max"] - 10, 0)
        self.player.energy = max(stats["energy_max"] - 10, 0)
        self.player.stamina = max(stats["stamina_max"] - 10, 0)
        self.player.save(update_fields=["health", "energy", "stamina"])

        with capture_game_messages() as messages:
            dispatch_text_command_as_mob(mob.id, f"/regen {self.player.key} health")

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, stats["health_max"])
        self.assertEqual(self.player.energy, max(stats["energy_max"] - 10, 0))
        self.assertEqual(self.player.stamina, max(stats["stamina_max"] - 10, 0))
        self.assertIsNotNone(self._message_by_type(messages, "cmd./regen.success", mob.key))
        notification = self._message_by_type(messages, "notification.regen", self.player.key)
        self.assertIsNotNone(notification)
        self.assertEqual(notification["data"]["actor"]["health"], stats["health_max"])

    def test_builder_regen_rejects_unknown_resource(self):
        stats = compute_stats(self.player.level, self.player.archetype, char=self.player)
        self.player.health = max(stats["health_max"] - 10, 0)
        self.player.save(update_fields=["health"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "/regen self focus")

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, max(stats["health_max"] - 10, 0))
        message = self._message_by_type(messages, "cmd./regen.error", self.player.key)
        self.assertIsNotNone(message)
        self.assertEqual(message["data"]["code"], "invalid_resource")
