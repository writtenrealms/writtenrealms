from unittest.mock import patch
from datetime import timedelta

from config import constants as adv_consts
from core.combat_formulas import (
    combatant_snapshot,
    normalize_combat_system,
    resolve_attack,
)
from core.computations import compute_stats
from django.utils import timezone
from spawns.actions.movement_costs import movement_cost
from spawns.actions.combat import (
    resolve_combat_encounter_step,
    resolve_due_character_effects,
)
from spawns.models import ActiveEffect, CombatEncounter, Item, Mob, Player
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
        self.spawn_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=["lifecycle"])
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

    def _periodic_effect(self, *, source, target, remaining_rounds=2, multiplier=1):
        source_stats = combatant_snapshot(source, world=source.world)
        return ActiveEffect.objects.create(
            world=self.spawn_world,
            encounter=CombatEncounter.objects.filter(
                player=self.player,
                status=CombatEncounter.STATUS_ACTIVE,
            ).first(),
            source_player=source if isinstance(source, Player) else None,
            source_mob=source if isinstance(source, Mob) else None,
            target_player=target if isinstance(target, Player) else None,
            target_mob=target if isinstance(target, Mob) else None,
            scope=ActiveEffect.SCOPE_CHARACTER,
            effect="dot",
            category="debuff",
            label="Burning Curse",
            remaining_rounds=remaining_rounds,
            duration_rounds=remaining_rounds,
            tick={
                "every_rounds": 1,
                "component": {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": multiplier},
                    "text": {"label": "Burning Curse"},
                },
            },
            source_snapshot={
                "ref": {
                    "type": "mob" if isinstance(source, Mob) else "player",
                    "id": source.id,
                },
                "key": source.key,
                "name": source.name,
                "level": source_stats.level,
                "actor_type": source_stats.actor_type,
                "stats": source_stats.stats,
                "weapon_damage": source_stats.weapon_damage,
                "is_disarmed": source_stats.is_disarmed,
                "outgoing_damage_multiplier": source_stats.outgoing_damage_multiplier,
            },
            is_hostile=True,
            next_tick_ts=timezone.now() - timedelta(seconds=1),
        )

    def test_player_dot_survives_flee_and_awards_remote_kill_credit(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        mob = self._mob()
        mob.fights_back = False
        mob.health = self.stats["attack_power"] + 1
        mob.health_max = mob.health
        mob.exp_worth = 7
        mob.gold = 3
        mob.save(update_fields=["fights_back", "health", "health_max", "exp_worth", "gold"])
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            resolution_interval=-1,
        )
        effect = self._periodic_effect(source=self.player, target=mob)
        effect.encounter = encounter
        effect.save(update_fields=["encounter"])
        starting_experience = self.player.experience
        starting_gold = self.player.gold

        dispatch_text_command(self.player.id, "flee")
        dispatch_text_command(self.player.id, "flee")

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        effect.refresh_from_db()
        self.assertEqual(self.player.room_id, self.escape_room.id)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(effect.remaining_rounds, 1)
        self.assertGreater(effect.next_tick_ts, timezone.now())
        self.assertEqual(resolve_due_character_effects(), [])
        self.assertTrue(Mob.objects.filter(pk=mob.id).exists())
        effect.next_tick_ts = timezone.now() - timedelta(seconds=1)
        effect.save(update_fields=["next_tick_ts"])

        events = resolve_due_character_effects()

        self.player.refresh_from_db()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(self.player.room_id, self.escape_room.id)
        self.assertEqual(self.player.experience, starting_experience + 7)
        self.assertEqual(self.player.gold, starting_gold + 3)
        corpse = Item.objects.get(type=adv_consts.ITEM_TYPE_CORPSE, container_id=self.room.id)
        self.assertIn("rat", corpse.name)
        death_event = next(
            event
            for event in events
            if event.type == "notification.death" and self.player.key in event.recipients
        )
        self.assertTrue(death_event.data["remote"])
        self.assertNotIn("room", death_event.data)
        self.assertNotIn("killer", death_event.data)
        self.assertEqual(death_event.data["corpse"]["key"], "")
        quest_event = next(event for event in events if event.type == "quest.mob.killed")
        self.assertEqual(quest_event.data["actor"]["key"], self.player.key)
        self.assertEqual(quest_event.data["target"]["id"], mob.id)

    def test_reengaged_mob_dot_kill_credits_original_player(self):
        mob = self._mob()
        mob.fights_back = False
        mob.health = 1
        mob.health_max = 1
        mob.exp_worth = 7
        mob.gold = 3
        mob.save(update_fields=["fights_back", "health", "health_max", "exp_worth", "gold"])
        origin_encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_FINISHED,
            resolution_interval=-1,
        )
        effect = self._periodic_effect(source=self.player, target=mob)
        effect.encounter = origin_encounter
        effect.save(update_fields=["encounter"])
        second_player = self.create_player("Ally")
        second_player.in_game = True
        second_player.save(update_fields=["in_game"])
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=second_player,
            mob=mob,
            resolution_interval=-1,
        )
        source_experience = self.player.experience
        source_gold = self.player.gold
        second_experience = second_player.experience
        second_gold = second_player.gold

        resolve_combat_encounter_step(encounter.id, auto_advance=False)

        self.player.refresh_from_db()
        second_player.refresh_from_db()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(self.player.experience, source_experience + 7)
        self.assertEqual(self.player.gold, source_gold + 3)
        self.assertEqual(second_player.experience, second_experience)
        self.assertEqual(second_player.gold, second_gold)

    def test_vanished_player_dot_does_not_credit_current_fighter(self):
        mob = self._mob()
        mob.fights_back = False
        mob.health = 1
        mob.health_max = 1
        mob.exp_worth = 7
        mob.gold = 3
        mob.save(update_fields=["fights_back", "health", "health_max", "exp_worth", "gold"])
        effect = self._periodic_effect(source=self.player, target=mob)
        second_player = self.create_player("Ally")
        second_player.in_game = True
        second_player.save(update_fields=["in_game"])
        self.player.delete()
        effect.refresh_from_db()
        self.assertIsNone(effect.source_player_id)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=second_player,
            mob=mob,
            resolution_interval=-1,
        )
        second_experience = second_player.experience
        second_gold = second_player.gold

        resolve_combat_encounter_step(encounter.id, auto_advance=False)

        second_player.refresh_from_db()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(second_player.experience, second_experience)
        self.assertEqual(second_player.gold, second_gold)

    def test_mob_dot_survives_flee_and_can_kill_player_in_new_room(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        mob = self._mob()
        mob.fights_back = False
        mob.save(update_fields=["fights_back"])
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            resolution_interval=-1,
        )
        effect = self._periodic_effect(source=mob, target=self.player)
        effect.encounter = encounter
        effect.save(update_fields=["encounter"])

        dispatch_text_command(self.player.id, "flee")
        dispatch_text_command(self.player.id, "flee")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.escape_room.id)
        self.player.health = 1
        self.player.save(update_fields=["health"])
        effect.refresh_from_db()
        effect.next_tick_ts = timezone.now() - timedelta(seconds=1)
        effect.save(update_fields=["next_tick_ts"])

        events = resolve_due_character_effects()

        self.player.refresh_from_db()
        self.assertGreater(self.player.health, 0)
        self.assertFalse(ActiveEffect.objects.filter(target_player=self.player).exists())
        death_event = next(event for event in events if event.type == "affect.death")
        self.assertEqual(death_event.data["killer"]["key"], mob.key)
        self.assertEqual(death_event.data["origin_room"]["id"], self.escape_room.id)

    def test_mob_dot_keeps_ticking_from_snapshot_after_source_is_deleted(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        mob = self._mob()
        mob.fights_back = False
        mob.save(update_fields=["fights_back"])
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            resolution_interval=-1,
        )
        effect = self._periodic_effect(source=mob, target=self.player)
        effect.encounter = encounter
        effect.save(update_fields=["encounter"])

        dispatch_text_command(self.player.id, "flee")
        dispatch_text_command(self.player.id, "flee")
        effect.refresh_from_db()
        effect.next_tick_ts = timezone.now() - timedelta(seconds=1)
        effect.save(update_fields=["next_tick_ts"])
        self.player.refresh_from_db()
        expected_damage = resolve_attack(
            actor=mob,
            target=self.player,
            world=self.spawn_world,
            profile_key="basic_physical",
            overrides={"multiplier": 1},
        ).damage_taken
        mob.delete()
        starting_health = self.player.health

        events = resolve_due_character_effects()

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, starting_health - expected_damage)
        self.assertFalse(ActiveEffect.objects.filter(pk=effect.id).exists())
        self.assertFalse(
            any(event.type == "notification.combat.attack" for event in events)
        )
        tick_event = next(
            event
            for event in events
            if event.type == "notification.combat.effect"
            and self.player.key in event.recipients
        )
        self.assertTrue(tick_event.data["remote"])
        self.assertEqual(tick_event.data["target"]["state"], "standing")
        state_event = next(
            event for event in events if event.type == "player.abilities.update"
        )
        self.assertEqual(state_event.data["actor"]["health"], self.player.health)
        self.assertEqual(state_event.data["actor"]["active_effects"], [])

    def test_deleting_encounter_only_removes_encounter_scoped_effects(self):
        mob = self._mob()
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
        )
        character_effect = self._periodic_effect(source=self.player, target=mob)
        character_effect.encounter = encounter
        character_effect.save(update_fields=["encounter"])
        encounter_effect = ActiveEffect.objects.create(
            world=self.spawn_world,
            encounter=encounter,
            source_player=self.player,
            target_mob=mob,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            effect="stun",
            category="debuff",
            label="Stun",
            remaining_rounds=1,
            duration_rounds=1,
        )

        encounter.delete()

        character_effect.refresh_from_db()
        self.assertIsNone(character_effect.encounter_id)
        self.assertFalse(ActiveEffect.objects.filter(pk=encounter_effect.id).exists())

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
