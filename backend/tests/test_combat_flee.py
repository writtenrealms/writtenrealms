import json
from datetime import timedelta
from unittest.mock import patch

from builders.currencies import create_currency
from builders.models import AbilityDefinition, MobDefinition, Trigger
from config import constants as adv_consts
from core.combat_formulas import (
    combatant_snapshot,
    normalize_combat_system,
    resolve_attack,
)
from core.computations import compute_stats
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from spawns.actions.movement_costs import movement_cost
from spawns.actions.combat import (
    FleeAction,
    resolve_combat_encounter_step,
    resolve_due_character_effects,
)
from spawns.models import ActiveEffect, CombatEncounter, Item, Mob, Player
from spawns.wallet import balance_map
from spawns.tasks import resolve_combat_encounter
from tests.base import WorldTestCase
from worlds.models import Room
from tests.utils import (
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)


class TestCombatFlee(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.currency = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
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

    def _guarded_exit_policy(self, definition, *, direction="east", room=None):
        room = room or self.room
        room_ct = ContentType.objects.get_for_model(Room)
        return Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_POLICY,
            target_type=room_ct,
            target_id=room.id,
            event=adv_consts.TRIGGER_EVENT_BEFORE_MOVE_EXIT,
            match=direction,
            conditions=json.dumps({
                "not": {
                    "mob_present": f"mobdefinition.{definition.slug}",
                },
            }),
            failure_message="The guard bars the eastern way.",
            display_action_in_room=False,
            gate_delay=0,
        )

    def _guard(self, definition):
        return Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=definition,
            name=definition.name,
            keywords="guard",
            health=10,
            health_max=10,
            fights_back=False,
        )

    def _active_encounter(self, mob, *, pending_flee=None):
        return CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            resolution_interval=1.5,
            pending_flee=pending_flee or {},
        )

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

    def _prevent_flee_effect(
        self,
        *,
        encounter,
        scope=ActiveEffect.SCOPE_CHARACTER,
        remaining_rounds=1,
        effect="silken-bind",
        label="Silken Bind",
        started_round=0,
        started_round_id="",
    ):
        return ActiveEffect.objects.create(
            world=self.spawn_world,
            encounter=encounter,
            target_player=self.player,
            scope=scope,
            effect=effect,
            category="debuff",
            label=label,
            remaining_rounds=remaining_rounds,
            duration_rounds=remaining_rounds,
            started_round=started_round,
            started_round_id=started_round_id,
            primitives=[
                {
                    "type": "action_rule",
                    "phase": "before_action",
                    "rule": "prevent",
                    "actions": ["flee"],
                    "reason": "rooted",
                }
            ],
        )

    def test_player_dot_survives_flee_and_awards_remote_kill_credit(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        mob = self._mob()
        mob.fights_back = False
        mob.health = self.stats["attack_power"] + 1
        mob.health_max = mob.health
        mob.exp_worth = 7
        mob.currency_reward_snapshot = {"obol": 3}
        mob.save(update_fields=[
            "fights_back",
            "health",
            "health_max",
            "exp_worth",
            "currency_reward_snapshot",
        ])
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
        starting_balance = balance_map(self.player)["obol"]

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
        self.assertEqual(balance_map(self.player)["obol"], starting_balance + 3)
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
        mob.currency_reward_snapshot = {"obol": 3}
        mob.save(update_fields=[
            "fights_back",
            "health",
            "health_max",
            "exp_worth",
            "currency_reward_snapshot",
        ])
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
        source_balance = balance_map(self.player)["obol"]
        second_experience = second_player.experience
        second_balance = balance_map(second_player)["obol"]

        resolve_combat_encounter_step(encounter.id, auto_advance=False)

        self.player.refresh_from_db()
        second_player.refresh_from_db()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(self.player.experience, source_experience + 7)
        self.assertEqual(balance_map(self.player)["obol"], source_balance + 3)
        self.assertEqual(second_player.experience, second_experience)
        self.assertEqual(balance_map(second_player)["obol"], second_balance)

    def test_vanished_player_dot_does_not_credit_current_fighter(self):
        mob = self._mob()
        mob.fights_back = False
        mob.health = 1
        mob.health_max = 1
        mob.exp_worth = 7
        mob.currency_reward_snapshot = {"obol": 3}
        mob.save(update_fields=[
            "fights_back",
            "health",
            "health_max",
            "exp_worth",
            "currency_reward_snapshot",
        ])
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
        second_balance = balance_map(second_player)["obol"]

        resolve_combat_encounter_step(encounter.id, auto_advance=False)

        second_player.refresh_from_db()
        self.assertFalse(Mob.objects.filter(pk=mob.id).exists())
        self.assertEqual(second_player.experience, second_experience)
        self.assertEqual(balance_map(second_player)["obol"], second_balance)

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
        self.assertEqual(
            (
                self.player.health,
                self.player.energy,
                self.player.stamina,
            ),
            (1, 1, 1),
        )
        self.assertFalse(ActiveEffect.objects.filter(target_player=self.player).exists())
        death_event = next(event for event in events if event.type == "affect.death")
        self.assertEqual(
            (
                death_event.data["actor"]["health"],
                death_event.data["actor"]["energy"],
                death_event.data["actor"]["stamina"],
            ),
            (1, 1, 1),
        )
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

    def test_fresh_flee_is_blocked_before_route_or_state_mutation(self):
        primary_encounter = self._active_encounter(self._mob())
        secondary_encounter = self._active_encounter(self._mob())
        primary_encounter.pending_player_ability = {"ability": "held-player-cast"}
        primary_encounter.pending_mob_ability = {"ability": "held-mob-cast"}
        primary_encounter.save(
            update_fields=["pending_player_ability", "pending_mob_ability"]
        )
        effect = self._prevent_flee_effect(
            encounter=secondary_encounter,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            remaining_rounds=2,
            effect="silken-bind",
            label="Silken Bind",
        )
        starting_stamina = self.player.stamina

        with patch("spawns.actions.combat._choose_flee_destination") as choose:
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "flee")

        choose.assert_not_called()
        primary_encounter.refresh_from_db()
        secondary_encounter.refresh_from_db()
        self.player.refresh_from_db()
        effect.refresh_from_db()
        error = self._messages_by_type(messages, "cmd.flee.error")[0]
        self.assertEqual(error["text"], "Silken Bind prevents you from fleeing.")
        self.assertEqual(error["data"]["code"], "action_prevented")
        self.assertEqual(error["data"]["action"], "flee")
        self.assertEqual(error["data"]["effect"], "silken-bind")
        self.assertEqual(error["data"]["effect_id"], effect.id)
        self.assertEqual(error["data"]["effect_label"], "Silken Bind")
        self.assertEqual(
            error["data"]["effect_scope"],
            ActiveEffect.SCOPE_ENCOUNTER,
        )
        self.assertEqual(error["data"]["effect_remaining_rounds"], 2)
        self.assertEqual(error["data"]["effect_duration_rounds"], 2)
        self.assertEqual(error["data"]["reason"], "rooted")
        self.assertEqual(error["data"]["phase"], "before_action")
        self.assertEqual(primary_encounter.pending_flee, {})
        self.assertEqual(
            primary_encounter.pending_player_ability,
            {"ability": "held-player-cast"},
        )
        self.assertEqual(
            primary_encounter.pending_mob_ability,
            {"ability": "held-mob-cast"},
        )
        self.assertEqual(secondary_encounter.pending_flee, {})
        self.assertEqual(self.player.stamina, starting_stamina)
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(effect.remaining_rounds, 2)

    def test_manual_ready_flee_block_consumes_turn_and_expires_effect(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=["combat_resolution_interval"])
        mob = self._mob()
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            resolution_interval=-1,
        )

        dispatch_text_command(self.player.id, "flee")

        encounter.refresh_from_db()
        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual(encounter.pending_flee["status"], "ready")
        ready_round = encounter.round_number
        health_before_completion = self.player.health
        mob_health_before_completion = mob.health
        effect = self._prevent_flee_effect(
            encounter=encounter,
            remaining_rounds=1,
            started_round=ready_round,
            started_round_id=f"encounter:{encounter.id}:{ready_round}",
        )
        AbilityDefinition.objects.create(
            world=self.world,
            slug="snare-counter",
            name="Snare Counter",
            command_verbs=["snarecounter"],
            target={
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": False,
            },
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Snare Counter"},
                }
            ],
        )
        encounter.pending_player_ability = {"ability": "held-player-cast"}
        encounter.pending_mob_ability = {
            "ability": "snare-counter",
            "command": "snare-counter",
            "target": {"type": "player", "id": self.player.id},
            "queued_round": ready_round,
        }
        encounter.save(
            update_fields=["pending_player_ability", "pending_mob_ability"]
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "flee")

        encounter.refresh_from_db()
        self.player.refresh_from_db()
        mob.refresh_from_db()
        error = self._messages_by_type(messages, "cmd.flee.error")[0]
        self.assertEqual(error["data"]["code"], "action_prevented")
        self.assertEqual(error["data"]["action"], "flee")
        self.assertEqual(error["data"]["effect_id"], effect.id)
        self.assertEqual(
            error["data"]["round_id"],
            f"encounter:{encounter.id}:{ready_round + 1}",
        )
        mob_attacks = [
            message
            for message in self._messages_by_type(
                messages,
                "notification.combat.attack",
            )
            if message["data"]["actor"]["key"] == mob.key
        ]
        self.assertEqual(len(mob_attacks), 1)
        self.assertEqual(mob_attacks[0]["data"]["attack"], "snare-counter")
        self.assertEqual(encounter.round_number, ready_round + 1)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(encounter.pending_flee, {})
        self.assertEqual(encounter.pending_player_ability, {})
        self.assertEqual(encounter.pending_mob_ability, {})
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(self.player.stamina, self.stats["stamina_max"])
        self.assertLess(self.player.health, health_before_completion)
        self.assertEqual(mob.health, mob_health_before_completion)
        self.assertFalse(ActiveEffect.objects.filter(pk=effect.id).exists())
        self.assertFalse(
            self._messages_by_type(messages, "cmd.flee.success")
        )

    def test_scheduled_ready_flee_block_continues_full_round_pipeline(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        mob = self._mob()
        encounter = self._active_encounter(mob)

        dispatch_text_command(self.player.id, "flee")
        encounter.next_resolution_ts = timezone.now()
        encounter.save(update_fields=["next_resolution_ts"])
        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual(encounter.pending_flee["status"], "ready")
        ready_round = encounter.round_number
        root_effect = self._prevent_flee_effect(
            encounter=encounter,
            remaining_rounds=2,
            started_round=ready_round,
            started_round_id=f"encounter:{encounter.id}:{ready_round}",
        )
        damage_effect = self._periodic_effect(
            source=mob,
            target=self.player,
            remaining_rounds=2,
        )
        self.player.ability_cooldowns = {"cooling-ability": 2}
        self.player.save(update_fields=["ability_cooldowns"])
        encounter.pending_player_ability = {"ability": "held-player-cast"}
        encounter.next_resolution_ts = timezone.now()
        encounter.save(
            update_fields=["pending_player_ability", "next_resolution_ts"]
        )
        health_before_completion = self.player.health
        mob_health_before_completion = mob.health

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        self.player.refresh_from_db()
        mob.refresh_from_db()
        root_effect.refresh_from_db()
        damage_effect.refresh_from_db()
        error = self._messages_by_type(messages, "cmd.flee.error")[0]
        self.assertEqual(error["data"]["code"], "action_prevented")
        self.assertEqual(
            error["data"]["round_id"],
            f"encounter:{encounter.id}:{ready_round + 1}",
        )
        self.assertEqual(encounter.round_number, ready_round + 1)
        self.assertEqual(encounter.status, CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(encounter.pending_flee, {})
        self.assertEqual(encounter.pending_player_ability, {})
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(self.player.stamina, self.stats["stamina_max"])
        self.assertEqual(self.player.ability_cooldowns, {"cooling-ability": 1})
        self.assertEqual(root_effect.remaining_rounds, 1)
        self.assertEqual(damage_effect.remaining_rounds, 1)
        self.assertLess(self.player.health, health_before_completion)
        self.assertEqual(mob.health, mob_health_before_completion)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertTrue(
            any(attack["data"]["label"] == "Burning Curse" for attack in attacks)
        )
        self.assertTrue(
            any(attack["data"]["actor"]["key"] == mob.key for attack in attacks)
        )

    def test_scheduled_flee_skips_one_player_round_then_exits_before_damage(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        mob = self._mob()

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "kill rat")
            encounter = CombatEncounter.objects.get(
                player=self.player,
                mob=mob,
                status=CombatEncounter.STATUS_ACTIVE,
            )
            encounter.pending_player_ability = {
                "ability": "held-player-cast",
                "status": "casting",
            }
            encounter.save(update_fields=["pending_player_ability"])
            with capture_game_messages() as flee_messages:
                dispatch_text_command(self.player.id, "flee")

        encounter.refresh_from_db()
        self.assertEqual(encounter.pending_flee["status"], "preparing")
        self.assertEqual(encounter.pending_player_ability, {})
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
        preparation_updates = self._messages_by_type(
            flee_messages,
            "player.ability_preparations.update",
        )
        self.assertEqual(
            [update["data"]["abilities"] for update in preparation_updates],
            [[]],
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

    def test_flee_excludes_direction_guarded_by_present_mob(self):
        north_room = self.room.create_at(adv_consts.DIRECTION_NORTH)
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="east-gate-guard",
            name="East Gate Guard",
        )
        self._guarded_exit_policy(definition)
        self._guard(definition)
        encounter = self._active_encounter(self._mob())

        dispatch_text_command(self.player.id, "flee")

        encounter.refresh_from_db()
        self.assertEqual(encounter.pending_flee["direction"], "north")
        self.assertEqual(encounter.pending_flee["destination_room_id"], north_room.id)

    def test_flee_reports_guard_policy_when_it_blocks_only_exit(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="east-gate-guard",
            name="East Gate Guard",
        )
        self._guarded_exit_policy(definition)
        self._guard(definition)
        encounter = self._active_encounter(self._mob())
        starting_stamina = self.player.stamina

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "flee")

        encounter.refresh_from_db()
        self.player.refresh_from_db()
        error = self._messages_by_type(messages, "cmd.flee.error")[0]
        self.assertEqual(error["text"], "The guard bars the eastern way.")
        self.assertEqual(error["data"]["code"], "policy_blocked")
        self.assertEqual(encounter.pending_flee, {})
        self.assertEqual(self.player.stamina, starting_stamina)

    def test_flee_reroutes_when_guard_arrives_during_preparation(self):
        north_room = self.room.create_at(adv_consts.DIRECTION_NORTH)
        north_room.type = adv_consts.ROOM_TYPE_ROAD
        north_room.save(update_fields=["type"])
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="east-gate-guard",
            name="East Gate Guard",
        )
        self._guarded_exit_policy(definition)
        encounter = self._active_encounter(self._mob())

        with patch(
            "spawns.actions.combat.random.choice",
            side_effect=lambda choices: next(
                choice for choice in choices if choice.direction == "east"
            ),
        ):
            dispatch_text_command(self.player.id, "flee")
        encounter.refresh_from_db()
        self.assertEqual(encounter.pending_flee["direction"], "east")
        reserved_cost = encounter.pending_flee["movement_cost"]
        encounter.pending_flee = {**encounter.pending_flee, "status": "ready"}
        encounter.save(update_fields=["pending_flee"])
        self._guard(definition)

        result = resolve_combat_encounter_step(encounter.id, auto_advance=False)

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        flee_event = next(event for event in result.events if event.type == "cmd.flee.success")
        self.assertEqual(self.player.room_id, north_room.id)
        self.assertEqual(flee_event.data["direction"], "north")
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(
            self.player.stamina,
            self.stats["stamina_max"] - movement_cost(north_room),
        )
        self.assertGreater(reserved_cost, movement_cost(north_room))

    def test_flee_completion_does_not_rescan_routes_when_stored_route_is_valid(self):
        encounter = self._active_encounter(self._mob())
        dispatch_text_command(self.player.id, "flee")
        encounter.refresh_from_db()
        encounter.pending_flee = {**encounter.pending_flee, "status": "ready"}
        encounter.save(update_fields=["pending_flee"])

        with patch("spawns.actions.combat._choose_flee_destination") as choose:
            result = resolve_combat_encounter_step(encounter.id, auto_advance=False)

        choose.assert_not_called()
        self.assertTrue(any(event.type == "cmd.flee.success" for event in result.events))

    def test_flee_stays_in_combat_and_refunds_cost_when_route_becomes_blocked(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="east-gate-guard",
            name="East Gate Guard",
        )
        policy = self._guarded_exit_policy(definition)
        encounter = self._active_encounter(self._mob())

        dispatch_text_command(self.player.id, "flee")
        encounter.refresh_from_db()
        self.assertEqual(encounter.pending_flee["direction"], "east")
        self.player.refresh_from_db()
        self.assertLess(self.player.stamina, self.stats["stamina_max"])
        encounter.pending_flee = {**encounter.pending_flee, "status": "ready"}
        encounter.save(update_fields=["pending_flee"])
        self._guard(definition)

        result = resolve_combat_encounter_step(encounter.id, auto_advance=False)

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        error = next(event for event in result.events if event.type == "cmd.flee.error")
        self.assertEqual(error.text, "The guard bars the eastern way.")
        self.assertEqual(error.data["code"], "policy_blocked")
        self.assertEqual(error.data["trigger_id"], policy.id)
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(self.player.stamina, self.stats["stamina_max"])
        self.assertEqual(encounter.status, CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(encounter.pending_flee, {})

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
            pending_player_ability={
                "ability": "held-secondary-cast",
                "status": "casting",
            },
        )

        dispatch_text_command(self.player.id, "flee")
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "flee")

        primary_encounter.refresh_from_db()
        secondary_encounter.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(primary_encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(secondary_encounter.status, CombatEncounter.STATUS_FINISHED)
        self.assertEqual(self.player.room_id, self.escape_room.id)
        preparation_updates = self._messages_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertEqual(
            [update["data"]["abilities"] for update in preparation_updates],
            [[]],
        )

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

    def test_stale_room_flee_finishes_charge_and_emits_clear_state(self):
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=self._mob(),
            pending_player_ability={
                "ability": "held-player-cast",
                "status": "casting",
            },
        )
        self.player.room = self.escape_room
        self.player.save(update_fields=["room"])

        with patch(
            "spawns.actions.combat.primary_active_encounter_for_player",
            return_value=encounter,
        ):
            result = FleeAction().execute(self.player.id)

        encounter.refresh_from_db()
        self.assertEqual(encounter.status, CombatEncounter.STATUS_FINISHED)
        error = next(event for event in result.events if event.type == "cmd.flee.error")
        self.assertEqual(error.data["code"], "combat_ended")
        preparation_state = next(
            event
            for event in result.events
            if event.type == "player.ability_preparations.update"
        )
        self.assertEqual(preparation_state.data["abilities"], [])

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
