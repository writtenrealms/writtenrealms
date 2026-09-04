from copy import deepcopy
import math
from unittest.mock import patch

from builders.models import (
    AbilityDefinition,
    ItemDefinition,
    MobDefinition,
    TrainerProfile,
    TrainerProfileAbility,
    Trigger,
)
from config import constants as adv_consts
from core.combat_formulas import CombatAttackResult, normalize_combat_system, resolve_attack
from core.computations import compute_stats
from core.scoped_state import STATE_SCOPE_CHARACTER, get_state_value
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from spawns.actions.combat import (
    CombatStepResult,
    _with_ability_prepare_transition,
)
from spawns.actions.abilities import (
    AbilityAction,
    SetAbilityHotkeyAction,
    resolve_ability_for_hotkey,
)
from spawns.actions.base import ActionError
from spawns.actions.movement_costs import movement_cost
from spawns.ability_intents import interruptible_ability_intent
from spawns.events import GameEvent
from spawns.models import ActiveEffect, CombatEncounter, Item, Mob, Player
from spawns.tasks import resolve_combat_encounter
from tests.base import WorldTestCase
from tests.utils import (
    BASIC_TEST_STAT_SYSTEM,
    apply_basic_stat_system,
    capture_game_messages,
    create_active_effect,
    dispatch_text_command,
    replace_active_effects,
)
from worlds.models import Room


class TestCombatAbilities(WorldTestCase):
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
        self.world.config.combat_resolution_interval = -1
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
                "basic_heal": {
                    "power_stat": "health_max",
                    "power_scale": 0.5,
                    "can_crit": False,
                    "variance": "none",
                    "minimum": 0,
                },
            },
        })
        self.world.config.save(update_fields=["combat_resolution_interval", "combat_system"])

    def _ability(
        self,
        *,
        slug,
        name,
        verbs,
        components,
        target=None,
        availability=None,
        requirements=None,
        cost=None,
        cast_time=None,
        cooldown=None,
        consumes_primary_action_on_resolve=True,
        consumes_primary_action_while_casting=True,
    ):
        return AbilityDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            command_verbs=verbs,
            consumes_primary_action_on_resolve=consumes_primary_action_on_resolve,
            consumes_primary_action_while_casting=consumes_primary_action_while_casting,
            target=target or {
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": False,
            },
            availability=availability or {"classes": [], "min_level": 1},
            requirements=requirements or {},
            cost=cost or {},
            cast_time=cast_time or {},
            cooldown=cooldown or {"rounds": 0},
            components=components,
        )

    def _mob(self, *, room=None, health=None, attack_power=0, fights_back=False, dodge=0):
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=room or self.room,
            name="Rat",
            keywords="rat",
            health=health or self.stats["attack_power"] * 10,
            health_max=health or self.stats["attack_power"] * 10,
            attack_power=attack_power,
            dodge=dodge,
            fights_back=fights_back,
            exp_worth=1,
        )
        mob.create_corpse()
        return mob

    def _trainer_definition(
        self,
        *,
        slug,
        name,
        abilities,
        availability="present",
    ):
        profile = TrainerProfile.objects.create(
            world=self.world,
            slug=f"{slug}-training",
            name=f"{name} Training",
        )
        TrainerProfileAbility.objects.bulk_create([
            TrainerProfileAbility(
                profile=profile,
                ability=ability,
                order=index,
            )
            for index, ability in enumerate(abilities)
        ])
        return MobDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            keywords="trainer arms",
            base_properties={"health_max": 10},
            trainer_profile=profile,
            trainer_availability=availability,
        )

    def _charge_ability(self):
        return self._ability(
            slug="charge",
            name="Charge",
            verbs=["charge"],
            target={
                "type": "hostile",
                "default": "current_target",
                "allow_out_of_combat": True,
                "range": "current_or_adjacent_room",
                "move_actor": True,
                "opener_priority": True,
            },
            cooldown={"rounds": 10},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 1.5},
                    "text": {"label": "Charge"},
                }
            ],
        )

    def _kick_ability(self):
        return self._ability(
            slug="kick",
            name="Kick",
            verbs=["kick"],
            cast_time={"rounds": 0},
            cooldown={"rounds": 12},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 0.25},
                    "text": {"label": "Kick"},
                },
                {
                    "type": "interrupt",
                    "target": "ability.target",
                    "apply": "on_hit",
                    "text": {"label": "Kick"},
                },
            ],
        )

    def _mob_with_cast_ability(self):
        ability = self._ability(
            slug="mob-hex",
            name="Mob Hex",
            verbs=["mobhex"],
            cast_time={"rounds": 1},
            cooldown={"rounds": 7},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Mob Hex"},
                }
            ],
        )
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="hexer",
            name="a hexer",
            keywords="hexer",
            base_properties={
                "level": 1,
                "health_max": 200,
                "attack_power": 7,
                "weapon_damage": 0,
                "fights_back": True,
            },
            combat_abilities=[{"ability": ability.slug, "weight": 1}],
        )
        return definition.spawn(self.room, self.spawn_world), ability

    def _shout_ability(self):
        return self._ability(
            slug="shout",
            name="Shout",
            verbs=["shout"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            cooldown={"rounds": 12},
            components=[
                {
                    "type": "effect",
                    "effect": "shout",
                    "category": "buff",
                    "target": "room.allies",
                    "stack_key": "shout-damage-output",
                    "stacking": "refresh",
                    "duration": {"rounds": 4},
                    "primitives": [
                        {
                            "type": "combat_modifier",
                            "phase": "outgoing_damage",
                            "multiplier": 1.2,
                        }
                    ],
                    "text": {"label": "Shout"},
                }
            ],
        )

    def _cleave_ability(self, *, duration_rounds=1):
        return self._ability(
            slug="cleave",
            name="Cleave",
            verbs=["cleave"],
            consumes_primary_action_on_resolve=False,
            components=[
                {
                    "type": "effect",
                    "effect": "cleave",
                    "category": "buff",
                    "target": "self",
                    "stack_key": "cleave",
                    "stacking": "refresh",
                    "duration": {"rounds": duration_rounds},
                    "primitives": [
                        {
                            "type": "combat_modifier",
                            "phase": "attack_routine",
                            "attack_routine": {
                                "extra_mainhand_strikes": 1,
                                "strike": {
                                    "source": "cleave",
                                    "target": "room.secondary_hostile",
                                    "weapon_slot": "weapon",
                                    "damage_multiplier": 1,
                                    "label": "Cleave",
                                },
                            },
                        }
                    ],
                    "text": {"label": "Cleave"},
                }
            ],
        )

    def _messages_by_type(self, messages, message_type):
        return [
            msg["message"]
            for msg in messages
            if msg["player_key"] == self.player.key
            and msg["message"].get("type") == message_type
        ]

    def _player_first_initiative(self, mob):
        return [
            {
                "type": "player",
                "id": self.player.id,
                "key": self.player.key,
                "side": "player_party",
                "initiative": 20,
                "source": "test",
            },
            {
                "type": "mob",
                "id": mob.id,
                "key": mob.key,
                "side": "hostile",
                "initiative": 10,
                "source": "test",
            },
        ]

    def test_casting_and_future_channeling_intents_are_interruptible(self):
        for status, phase in (("casting", "cast"), ("channeling", "channel")):
            with self.subTest(status=status):
                intent = interruptible_ability_intent({
                    "ability": "long-action",
                    "status": status,
                })
                self.assertIsNotNone(intent)
                self.assertEqual(intent.slug, "long-action")
                self.assertEqual(intent.phase, phase)

        for status in ("queued", "", None):
            with self.subTest(status=status):
                self.assertIsNone(
                    interruptible_ability_intent({
                        "ability": "long-action",
                        "status": status,
                    })
                )

    def test_definition_backed_mob_uses_combat_ability_loadout(self):
        self._ability(
            slug="shadow-bolt",
            name="Shadow Bolt",
            verbs=["shadowbolt"],
            availability={"actors": ["mob"], "classes": [], "min_level": 1},
            cooldown={"rounds": 2},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Shadow Bolt"},
                }
            ],
        )
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="cave-shaman",
            name="a cave shaman",
            keywords="cave shaman",
            base_properties={
                "level": 1,
                "health_max": 200,
                "attack_power": 7,
                "weapon_damage": 0,
                "fights_back": True,
            },
            combat_abilities=[
                {
                    "ability": "shadow-bolt",
                    "weight": 1,
                }
            ],
        )
        mob = mob_definition.spawn(self.room, self.spawn_world)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("spawns.actions.combat.random.randint", return_value=1):
                with capture_game_messages() as messages:
                    resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        mob.refresh_from_db()
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        mob_ability_attacks = [
            attack
            for attack in attacks
            if attack["data"]["attack"] == "shadow-bolt"
        ]
        self.assertEqual(len(mob_ability_attacks), 1)
        self.assertEqual(mob_ability_attacks[0]["data"]["label"], "Shadow Bolt")
        self.assertEqual(mob_ability_attacks[0]["data"]["actor"]["key"], mob.key)
        self.assertEqual(mob_ability_attacks[0]["data"]["target"]["key"], self.player.key)
        self.assertEqual(mob.ability_cooldowns, {"shadow-bolt": 2})
        self.assertLess(self.player.health, self.stats["health_max"])

    def test_player_only_ability_is_skipped_by_mob_loadout(self):
        self._ability(
            slug="player-strike",
            name="Player Strike",
            verbs=["playerstrike"],
            availability={"actors": ["player"], "classes": [], "min_level": 1},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Player Strike"},
                }
            ],
        )
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="misconfigured-mob",
            name="a misconfigured mob",
            keywords="misconfigured mob",
            base_properties={
                "level": 1,
                "health_max": 200,
                "attack_power": 7,
                "weapon_damage": 0,
                "fights_back": True,
            },
            combat_abilities=[{"ability": "player-strike", "weight": 1}],
        )
        mob = mob_definition.spawn(self.room, self.spawn_world)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("spawns.actions.combat.random.randint", return_value=1):
                with capture_game_messages() as messages:
                    resolve_combat_encounter(encounter.id)

        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertFalse(
            any(attack["data"]["attack"] == "player-strike" for attack in attacks)
        )
        self.assertTrue(
            any(attack["data"]["actor"]["key"] == mob.key for attack in attacks)
        )

    def test_mob_only_stale_known_ability_cannot_be_hotkeyed_or_executed(self):
        ability = self._ability(
            slug="mob-curse",
            name="Mob Curse",
            verbs=["mobcurse"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            availability={"actors": ["mob"], "classes": [], "min_level": 1},
            components=[{"type": "healing", "profile": "basic_heal"}],
        )
        self.player.known_abilities = [ability.slug]
        self.player.ability_hotkeys = {"1": ability.slug}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])

        self.assertIsNone(resolve_ability_for_hotkey(self.player, 1))
        with self.assertRaises(ActionError) as hotkey_error:
            SetAbilityHotkeyAction().execute(self.player.id, 2, ability.slug)
        self.assertEqual(hotkey_error.exception.code, "ability_missing")
        with self.assertRaises(ActionError) as execute_error:
            AbilityAction().execute(
                self.player.id,
                ability=ability,
                command="mobcurse",
                args=[],
            )
        self.assertEqual(execute_error.exception.code, "ability_unavailable")

        from spawns.state_payloads import build_state_sync

        payload = build_state_sync(self.player).model_dump()
        self.assertNotIn(
            ability.slug,
            payload["world"]["abilities"]["definitions"],
        )

    def test_mob_cast_can_consume_charge_round_but_not_resolution_round(self):
        self._ability(
            slug="mob-crack",
            name="Crack",
            verbs=["crack"],
            cast_time={"rounds": 1},
            cooldown={"rounds": 2},
            consumes_primary_action_on_resolve=False,
            consumes_primary_action_while_casting=True,
            components=[
                {
                    "type": "effect",
                    "effect": "stun",
                    "target": "ability.target",
                    "duration": {"rounds": 1},
                    "apply": "on_resolve",
                    "text": {"label": "Crack"},
                }
            ],
        )
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="persian-slinger",
            name="a Persian slinger",
            keywords="persian slinger",
            base_properties={
                "level": 1,
                "health_max": 200,
                "attack_power": 7,
                "weapon_damage": 0,
                "fights_back": True,
            },
            combat_abilities=[{"ability": "mob-crack", "weight": 1}],
        )
        mob = mob_definition.spawn(self.room, self.spawn_world)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("spawns.actions.combat.random.randint", return_value=1):
                with capture_game_messages() as charge_messages:
                    resolve_combat_encounter(encounter.id)

        casts = self._messages_by_type(
            charge_messages,
            "notification.combat.ability_casting",
        )
        charge_round_mob_attacks = [
            attack
            for attack in self._messages_by_type(
                charge_messages,
                "notification.combat.attack",
            )
            if attack["data"]["actor"]["key"] == mob.key
        ]
        self.assertEqual(len(casts), 1)
        self.assertEqual(charge_round_mob_attacks, [])

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("spawns.actions.combat.random.randint", return_value=1):
                with capture_game_messages() as resolve_messages:
                    resolve_combat_encounter(encounter.id)

        effects = self._messages_by_type(
            resolve_messages,
            "notification.combat.effect",
        )
        resolve_round_mob_attacks = [
            attack
            for attack in self._messages_by_type(
                resolve_messages,
                "notification.combat.attack",
            )
            if attack["data"]["actor"]["key"] == mob.key
        ]
        mob.refresh_from_db()
        self.assertTrue(
            any(effect["data"]["label"] == "Crack" for effect in effects)
        )
        combat_effect_updates = self._messages_by_type(
            resolve_messages,
            "player.combat_effects.update",
        )
        self.assertEqual(
            combat_effect_updates[-1]["data"]["active_effects"][0]["label"],
            "Crack",
        )
        self.assertEqual(
            combat_effect_updates[-1]["data"]["active_effects"][0]["remaining_rounds"],
            1,
        )
        self.assertEqual(len(resolve_round_mob_attacks), 1)
        self.assertEqual(mob.ability_cooldowns, {"mob-crack": 2})

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("spawns.actions.combat.random.randint", return_value=1):
                with capture_game_messages() as stunned_messages:
                    resolve_combat_encounter(encounter.id)

        stun_events = self._messages_by_type(
            stunned_messages,
            "notification.combat.effect",
        )
        self.assertTrue(
            any(
                event.get("text") == "You are stunned and cannot act."
                for event in stun_events
            )
        )
        combat_effect_updates = self._messages_by_type(
            stunned_messages,
            "player.combat_effects.update",
        )
        self.assertEqual(combat_effect_updates[-1]["data"]["active_effects"], [])

    def test_mob_cast_pipeline_applies_root_and_blocks_flee(self):
        self._ability(
            slug="mob-leg-irons",
            name="Leg Irons",
            verbs=["graspingroots"],
            cast_time={"rounds": 1},
            cooldown={"rounds": 7},
            consumes_primary_action_on_resolve=False,
            consumes_primary_action_while_casting=True,
            components=[
                {
                    "type": "effect",
                    "effect": "root",
                    "scope": "encounter",
                    "category": "debuff",
                    "target": "ability.target",
                    "duration": {"rounds": 4},
                    "apply": "on_resolve",
                    "primitives": [
                        {
                            "type": "action_rule",
                            "phase": "before_action",
                            "rule": "prevent",
                            "actions": ["flee"],
                            "reason": "rooted",
                        }
                    ],
                    "text": {"label": "Rooted"},
                }
            ],
        )
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="briar-witch",
            name="a briar witch",
            keywords="briar witch",
            base_properties={
                "level": 1,
                "health_max": 200,
                "attack_power": 7,
                "weapon_damage": 0,
                "fights_back": True,
            },
            combat_abilities=[{"ability": "mob-leg-irons", "weight": 1}],
        )
        mob = mob_definition.spawn(self.room, self.spawn_world)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )
        escape_room = self.room.create_at(adv_consts.DIRECTION_EAST)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("spawns.actions.combat.random.randint", return_value=1):
                resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual(
            encounter.pending_mob_ability,
            {
                "ability": "mob-leg-irons",
                "command": "mob-leg-irons",
                "target": {"type": "player", "id": self.player.id},
                "queued_round": 1,
                "status": "casting",
                "cast_rounds_remaining": 0,
            },
        )
        self.assertEqual(mob.ability_cooldowns, {})
        self.assertFalse(
            ActiveEffect.objects.filter(
                encounter=encounter,
                target_player=self.player,
                effect="root",
            ).exists()
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("spawns.actions.combat.random.randint", return_value=1):
                resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        mob.refresh_from_db()
        root_effect = ActiveEffect.objects.get(
            encounter=encounter,
            target_player=self.player,
            source_mob=mob,
            effect="root",
        )
        self.assertEqual(root_effect.scope, ActiveEffect.SCOPE_ENCOUNTER)
        self.assertEqual(root_effect.remaining_rounds, 4)
        self.assertEqual(root_effect.duration_rounds, 4)
        self.assertEqual(
            root_effect.primitives,
            [
                {
                    "type": "action_rule",
                    "phase": "before_action",
                    "rule": "prevent",
                    "actions": ["flee"],
                    "reason": "rooted",
                }
            ],
        )
        self.assertEqual(encounter.pending_mob_ability, {})
        self.assertEqual(mob.ability_cooldowns, {"mob-leg-irons": 7})

        starting_stamina = self.player.stamina
        with capture_game_messages() as flee_messages:
            dispatch_text_command(self.player.id, "flee")

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        flee_errors = self._messages_by_type(flee_messages, "cmd.flee.error")
        self.assertEqual(len(flee_errors), 1)
        self.assertEqual(flee_errors[0]["text"], "Rooted prevents you from fleeing.")
        self.assertEqual(flee_errors[0]["data"]["code"], "action_prevented")
        self.assertEqual(flee_errors[0]["data"]["effect_id"], root_effect.id)
        self.assertEqual(flee_errors[0]["data"]["reason"], "rooted")
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertNotEqual(self.player.room_id, escape_room.id)
        self.assertEqual(self.player.stamina, starting_stamina)
        self.assertEqual(encounter.pending_flee, {})

    def test_kick_interrupts_active_mob_cast_without_same_round_reselection(self):
        self._kick_ability()
        self.player.known_abilities = ["kick"]
        self.player.save(update_fields=["known_abilities"])
        mob, mob_ability = self._mob_with_cast_ability()
        starting_health = mob.health
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
            pending_player_ability={
                "ability": "kick",
                "command": "kick",
                "target": {"type": "mob", "id": mob.id},
                "queued_round": 0,
            },
            pending_mob_ability={
                "ability": mob_ability.slug,
                "command": mob_ability.slug,
                "target": {"type": "player", "id": self.player.id},
                "queued_round": 0,
                "status": "casting",
                "cast_rounds_remaining": 0,
            },
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        mob.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(
            starting_health - mob.health,
            math.ceil(self.stats["attack_power"] * 0.25),
        )
        self.assertEqual(encounter.pending_mob_ability, {})
        self.assertEqual(mob.ability_cooldowns, {})
        self.assertEqual(self.player.ability_cooldowns, {"kick": 12})
        mob_hex_attacks = [
            attack
            for attack in self._messages_by_type(
                messages,
                "notification.combat.attack",
            )
            if attack["data"]["attack"] == mob_ability.slug
        ]
        self.assertEqual(mob_hex_attacks, [])
        interrupts = self._messages_by_type(
            messages,
            "notification.combat.ability_interrupted",
        )
        self.assertEqual(len(interrupts), 1)
        self.assertEqual(interrupts[0]["data"]["ability"]["slug"], "kick")
        self.assertEqual(
            interrupts[0]["data"]["interrupted_ability"],
            {
                "slug": mob_ability.slug,
                "status": "casting",
                "phase": "cast",
            },
        )
        self.assertEqual(
            interrupts[0]["data"]["round_id"],
            f"encounter:{encounter.id}:1",
        )

    def test_kick_does_not_interrupt_an_ability_that_is_only_queued(self):
        self._kick_ability()
        self.player.known_abilities = ["kick"]
        self.player.save(update_fields=["known_abilities"])
        mob, mob_ability = self._mob_with_cast_ability()
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
            pending_player_ability={
                "ability": "kick",
                "command": "kick",
                "target": {"type": "mob", "id": mob.id},
                "queued_round": 0,
            },
            pending_mob_ability={
                "ability": mob_ability.slug,
                "command": mob_ability.slug,
                "target": {"type": "player", "id": self.player.id},
                "queued_round": 0,
                "status": "queued",
                "cast_rounds_remaining": 1,
            },
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        self.assertEqual(encounter.pending_mob_ability["status"], "casting")
        self.assertEqual(
            encounter.pending_mob_ability["cast_rounds_remaining"],
            0,
        )
        self.assertEqual(
            self._messages_by_type(
                messages,
                "notification.combat.ability_interrupted",
            ),
            [],
        )

    def test_kick_on_hit_interrupt_does_not_cancel_cast_when_dodged(self):
        kick = self._kick_ability()
        kick.components[0]["overrides"].update({
            "can_dodge": True,
            "can_crit": False,
        })
        kick.save(update_fields=["components"])
        self.player.known_abilities = [kick.slug]
        self.player.save(update_fields=["known_abilities"])
        mob, mob_ability = self._mob_with_cast_ability()
        mob.dodge = 100000
        mob.save(update_fields=["dodge"])
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
            pending_player_ability={
                "ability": kick.slug,
                "command": kick.slug,
                "target": {"type": "mob", "id": mob.id},
                "queued_round": 0,
            },
            pending_mob_ability={
                "ability": mob_ability.slug,
                "command": mob_ability.slug,
                "target": {"type": "player", "id": self.player.id},
                "queued_round": 0,
                "status": "casting",
                "cast_rounds_remaining": 0,
            },
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("core.combat_formulas.random.random", return_value=0):
                with capture_game_messages() as messages:
                    resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        mob.refresh_from_db()
        attacks = self._messages_by_type(
            messages,
            "notification.combat.attack",
        )
        kick_attacks = [
            attack for attack in attacks if attack["data"]["attack"] == kick.slug
        ]
        self.assertEqual(kick_attacks[0]["data"]["outcome"], "dodged")
        self.assertTrue(
            any(attack["data"]["attack"] == mob_ability.slug for attack in attacks)
        )
        self.assertEqual(
            self._messages_by_type(
                messages,
                "notification.combat.ability_interrupted",
            ),
            [],
        )
        self.assertEqual(self.player.ability_cooldowns, {kick.slug: 12})
        self.assertEqual(mob.ability_cooldowns, {mob_ability.slug: 7})

    def test_mob_ability_chance_failure_falls_back_to_basic_attack(self):
        self._ability(
            slug="shadow-bolt",
            name="Shadow Bolt",
            verbs=["shadowbolt"],
            cooldown={"rounds": 2},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Shadow Bolt"},
                }
            ],
        )
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="cave-shaman",
            name="a cave shaman",
            keywords="cave shaman",
            base_properties={
                "level": 1,
                "health_max": 200,
                "attack_power": 7,
                "weapon_damage": 0,
                "fights_back": True,
            },
            combat_abilities=[
                {
                    "ability": "shadow-bolt",
                    "weight": 1,
                    "chance": 25,
                }
            ],
        )
        mob = mob_definition.spawn(self.room, self.spawn_world)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("spawns.actions.combat.random.randint", return_value=26):
                with capture_game_messages() as messages:
                    resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        mob.refresh_from_db()
        mob_attacks = [
            attack
            for attack in self._messages_by_type(messages, "notification.combat.attack")
            if attack["data"]["actor"]["key"] == mob.key
        ]
        self.assertEqual(len(mob_attacks), 1)
        self.assertNotEqual(mob_attacks[0]["data"]["attack"], "shadow-bolt")
        self.assertEqual(mob.ability_cooldowns, {})
        self.assertLess(self.player.health, self.stats["health_max"])

    def test_mob_ability_chance_success_selects_ability(self):
        self._ability(
            slug="shadow-bolt",
            name="Shadow Bolt",
            verbs=["shadowbolt"],
            cooldown={"rounds": 2},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Shadow Bolt"},
                }
            ],
        )
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="cave-shaman",
            name="a cave shaman",
            keywords="cave shaman",
            base_properties={
                "level": 1,
                "health_max": 200,
                "attack_power": 7,
                "weapon_damage": 0,
                "fights_back": True,
            },
            combat_abilities=[
                {
                    "ability": "shadow-bolt",
                    "weight": 1,
                    "chance": 25,
                }
            ],
        )
        mob = mob_definition.spawn(self.room, self.spawn_world)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        def chance_then_weight(low, high):
            return 25 if high == 100 else 1

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch(
                "spawns.actions.combat.random.randint",
                side_effect=chance_then_weight,
            ):
                with capture_game_messages() as messages:
                    resolve_combat_encounter(encounter.id)

        mob.refresh_from_db()
        mob_ability_attacks = [
            attack
            for attack in self._messages_by_type(messages, "notification.combat.attack")
            if attack["data"]["attack"] == "shadow-bolt"
        ]
        self.assertEqual(len(mob_ability_attacks), 1)
        self.assertEqual(mob.ability_cooldowns, {"shadow-bolt": 2})

    def test_percent_base_cost_uses_energy_base_before_equipment_modifiers(self):
        from spawns.actions.abilities import ability_cost_amount

        stat_system = deepcopy(BASIC_TEST_STAT_SYSTEM)
        stat_system["formulas"]["base_resources"]["energy"] = {"flat": 100}
        self.world.config.stat_system = stat_system
        self.world.config.save(update_fields=["stat_system"])

        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="focus-ring",
            name="Focus Ring",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_ACCESSORY,
            },
        )
        ring = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            definition=definition,
            definition_slug_snapshot=definition.slug,
            name=definition.name,
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=adv_consts.EQUIPMENT_TYPE_ACCESSORY,
            energy_max=100,
        )
        self.player.equipment.equip(ring, adv_consts.EQUIPMENT_SLOT_ACCESSORY)

        stats = compute_stats(
            self.player.level,
            self.player.archetype,
            char=self.player,
            world=self.world,
        )
        self.assertEqual(stats["energy_base"], 100)
        self.assertEqual(stats["energy_max"], 200)

        ability = self._ability(
            slug="arcane-bolt",
            name="Arcane Bolt",
            verbs=["bolt"],
            cost={"resource": "energy", "amount": 5, "calc": "percent_base"},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )

        self.assertEqual(ability_cost_amount(self.player, ability), ("energy", 5))

    def test_percent_base_cost_uses_health_and_stamina_base_before_equipment_modifiers(self):
        from spawns.actions.abilities import ability_cost_amount

        stat_system = deepcopy(BASIC_TEST_STAT_SYSTEM)
        stat_system["formulas"]["base_resources"]["health"] = {"flat": 100}
        stat_system["formulas"]["base_resources"]["stamina"] = {"flat": 100}
        stat_system["formulas"]["global_rules"] = []
        self.world.config.stat_system = stat_system
        self.world.config.save(update_fields=["stat_system"])

        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="vital-ring",
            name="Vital Ring",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "equipment_type": adv_consts.EQUIPMENT_TYPE_ACCESSORY,
            },
        )
        ring = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            definition=definition,
            definition_slug_snapshot=definition.slug,
            name=definition.name,
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=adv_consts.EQUIPMENT_TYPE_ACCESSORY,
            health_max=100,
            stamina_max=100,
        )
        self.player.equipment.equip(ring, adv_consts.EQUIPMENT_SLOT_ACCESSORY)

        stats = compute_stats(
            self.player.level,
            self.player.archetype,
            char=self.player,
            world=self.world,
        )
        self.assertEqual(stats["health_base"], 100)
        self.assertEqual(stats["health_max"], 200)
        self.assertEqual(stats["stamina_base"], 100)
        self.assertEqual(stats["stamina_max"], 200)

        health_ability = self._ability(
            slug="blood-price",
            name="Blood Price",
            verbs=["bloodprice"],
            cost={"resource": "health", "amount": 5, "calc": "percent_base"},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        stamina_ability = self._ability(
            slug="quick-step",
            name="Quick Step",
            verbs=["quickstep"],
            cost={"resource": "stamina", "amount": 5, "calc": "percent_base"},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )

        self.assertEqual(ability_cost_amount(self.player, health_ability), ("health", 5))
        self.assertEqual(ability_cost_amount(self.player, stamina_ability), ("stamina", 5))

    def test_learning_ability_assigns_next_available_hotkey(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "learn power strike")

        self.player.refresh_from_db()
        self.assertEqual(self.player.known_abilities, ["power-strike"])
        self.assertEqual(self.player.ability_hotkeys, {"1": "power-strike"})
        success = self._messages_by_type(messages, "cmd.ability.learn.success")[0]
        self.assertEqual(success["data"]["ability"]["hotkey"], "1")
        self.assertEqual(success["data"]["actor"]["ability_hotkeys"], {"1": "power-strike"})

    def test_learn_without_selector_requires_present_trainer(self):
        ability = self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self._trainer_definition(
            slug="arms-trainer",
            name="an arms trainer",
            abilities=[ability],
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "learn")

        errors = self._messages_by_type(messages, "cmd.ability.learn.error")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["text"], "There is no-one around to teach you right now.")
        self.assertEqual(errors[0]["data"]["code"], "ability_trainer_unavailable")

    def test_learn_without_selector_lists_ungated_abilities_without_trainer(self):
        self._ability(
            slug="field-mend",
            name="Field Mend",
            verbs=["mend", "fieldmend"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            components=[{"type": "healing", "profile": "basic_heal"}],
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "learn")

        lists = self._messages_by_type(messages, "cmd.ability.learn.list")
        self.assertEqual(len(lists), 1)
        self.assertEqual(
            lists[0]["text"],
            "You can learn here:\n"
            "1. Field Mend [ learn mend ]\n"
            "Use: learn <number>",
        )
        self.assertEqual(
            lists[0]["data"]["abilities"],
            [
                {
                    "number": 1,
                    "slug": "field-mend",
                    "name": "Field Mend",
                    "learn_command": "learn mend",
                    "trainer": None,
                    "learning": None,
                }
            ],
        )

    def test_learn_without_selector_lists_trainable_abilities_at_trainer(self):
        power_strike = self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        locked_strike = self._ability(
            slug="locked-strike",
            name="Locked Strike",
            verbs=["lockedstrike"],
            availability={"classes": [], "min_level": 99},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self._ability(
            slug="field-mend",
            name="Field Mend",
            verbs=["mend", "fieldmend"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            components=[{"type": "healing", "profile": "basic_heal"}],
        )
        trainer_definition = self._trainer_definition(
            slug="arms-trainer",
            name="an arms trainer",
            abilities=[power_strike, locked_strike],
        )
        trainer = trainer_definition.spawn(self.room, self.spawn_world)
        trainer_profile = trainer_definition.trainer_profile
        trainer_payload = {
            "type": "mob",
            "id": trainer.id,
            "key": trainer.key,
            "name": "an arms trainer",
            "profile": {
                "id": trainer_profile.id,
                "key": trainer_profile.key,
                "slug": trainer_profile.slug,
                "name": trainer_profile.name,
            },
            "learning": {
                "status": "unrestricted",
                "eligible": True,
                "max_known": None,
                "known": 0,
                "remaining": None,
            },
        }

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "learn")

        lists = self._messages_by_type(messages, "cmd.ability.learn.list")
        self.assertEqual(len(lists), 1)
        self.assertEqual(
            lists[0]["text"],
            "You can learn here:\n"
            "1. Power Strike [ learn strike ]\n"
            "2. Field Mend [ learn mend ]\n"
            "Use: learn <number>",
        )
        self.assertEqual(
            lists[0]["data"]["abilities"],
            [
                {
                    "number": 1,
                    "slug": "power-strike",
                    "name": "Power Strike",
                    "learn_command": "learn strike",
                    "trainer": trainer_payload,
                    "learning": trainer_payload["learning"],
                },
                {
                    "number": 2,
                    "slug": "field-mend",
                    "name": "Field Mend",
                    "learn_command": "learn mend",
                    "trainer": None,
                    "learning": None,
                },
            ],
        )
        self.assertEqual(
            lists[0]["data"]["trainers"],
            [trainer_payload],
        )

    def test_learning_trainer_gated_ability_requires_present_trainer(self):
        ability = self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        trainer_definition = self._trainer_definition(
            slug="arms-trainer",
            name="an arms trainer",
            abilities=[ability],
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "learn power strike")

        self.player.refresh_from_db()
        self.assertEqual(self.player.known_abilities, [])
        errors = self._messages_by_type(messages, "cmd.ability.learn.error")
        self.assertEqual(errors[0]["data"]["code"], "ability_trainer_required")

        trainer_definition.spawn(self.room, self.spawn_world)
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "learn power strike")

        self.player.refresh_from_db()
        self.assertEqual(self.player.known_abilities, ["power-strike"])
        success = self._messages_by_type(messages, "cmd.ability.learn.success")[0]
        self.assertEqual(success["data"]["trainer"]["name"], "an arms trainer")

    def test_unlearning_trainer_gated_ability_requires_present_trainer(self):
        ability = self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        trainer_definition = self._trainer_definition(
            slug="arms-trainer",
            name="an arms trainer",
            abilities=[ability],
        )
        self.player.known_abilities = ["power-strike"]
        self.player.ability_hotkeys = {"1": "power-strike"}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=self._mob(),
            pending_player_ability={
                "ability": "power-strike",
                "status": "queued",
            },
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "unlearn power strike")

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(self.player.known_abilities, ["power-strike"])
        self.assertEqual(self.player.ability_hotkeys, {"1": "power-strike"})
        self.assertEqual(
            encounter.pending_player_ability["status"],
            "queued",
        )
        errors = self._messages_by_type(messages, "cmd.ability.unlearn.error")
        self.assertEqual(errors[0]["data"]["code"], "ability_trainer_required")
        self.assertEqual(
            self._messages_by_type(
                messages,
                "player.ability_preparations.update",
            ),
            [],
        )

        trainer_definition.spawn(self.room, self.spawn_world)
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "unlearn power strike")

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(self.player.known_abilities, [])
        self.assertEqual(self.player.ability_hotkeys, {})
        self.assertEqual(encounter.pending_player_ability, {})
        success = self._messages_by_type(messages, "cmd.ability.unlearn.success")[0]
        self.assertEqual(success["data"]["trainer"]["name"], "an arms trainer")
        preparation_updates = self._messages_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertEqual(
            [update["data"]["abilities"] for update in preparation_updates],
            [[]],
        )

    def test_learning_ability_checks_condition_requirements(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            requirements={"eq": ["actor.archetype", "not-a-real-class"]},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "learn power strike")

        self.player.refresh_from_db()
        self.assertEqual(self.player.known_abilities, [])
        errors = self._messages_by_type(messages, "cmd.ability.learn.error")
        self.assertEqual(errors[0]["data"]["code"], "ability_unavailable")

    def test_using_ability_checks_condition_requirements(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            requirements={"eq": ["actor.archetype", "not-a-real-class"]},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self.player.known_abilities = ["power-strike"]
        self.player.save(update_fields=["known_abilities"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "strike")

        errors = self._messages_by_type(messages, "cmd.ability.error")
        self.assertEqual(errors[0]["data"]["code"], "ability_unavailable")

    def test_hotkey_command_reassigns_known_ability(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self._ability(
            slug="quick-jab",
            name="Quick Jab",
            verbs=["jab"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self.player.known_abilities = ["power-strike", "quick-jab"]
        self.player.ability_hotkeys = {"1": "power-strike", "2": "quick-jab"}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "hotkey 1 quick jab")

        self.player.refresh_from_db()
        self.assertEqual(self.player.ability_hotkeys, {"1": "quick-jab"})
        success = self._messages_by_type(messages, "cmd.ability.hotkey.success")[0]
        self.assertEqual(success["data"]["hotkey"], "1")
        self.assertEqual(success["data"]["replaced_ability"]["slug"], "power-strike")
        self.assertEqual(success["data"]["actor"]["ability_hotkeys"], {"1": "quick-jab"})

    def test_numbered_hotkey_uses_assigned_ability_and_reports_round_cooldown(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            cooldown={"rounds": 2},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 1},
                    "text": {"label": "Power Strike"},
                }
            ],
        )
        self.player.known_abilities = ["power-strike"]
        self.player.ability_hotkeys = {"1": "power-strike"}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])
        mob = self._mob(health=self.stats["attack_power"] * 10)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "1 rat")

        mob.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 9)
        self.assertEqual(self.player.ability_cooldowns, {"power-strike": 2})
        updates = self._messages_by_type(messages, "player.abilities.update")
        self.assertEqual(updates[-1]["data"]["actor"]["ability_cooldowns"], {"power-strike": 2})

    def test_on_hit_cooldown_does_not_start_when_ability_is_dodged(self):
        self._ability(
            slug="bash",
            name="Bash",
            verbs=["bash"],
            cooldown={"rounds": 6, "trigger": "on_hit"},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {
                        "multiplier": 1,
                        "can_dodge": True,
                        "can_crit": False,
                    },
                    "text": {"label": "Bash"},
                },
                {
                    "type": "effect",
                    "effect": "stun",
                    "duration": {"rounds": 2},
                    "apply": "on_hit",
                    "text": {"label": "Bash"},
                },
            ],
        )
        self.player.known_abilities = ["bash"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 10, dodge=100000)

        with patch("core.combat_formulas.random.random", return_value=0):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "bash rat")

        self.player.refresh_from_db()
        self.assertEqual(self.player.ability_cooldowns, {})
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[0]["data"]["outcome"], "dodged")
        effects = self._messages_by_type(messages, "notification.combat.effect")
        self.assertEqual(effects, [])

        mob.dodge = 0
        mob.save(update_fields=["dodge"])
        with patch("core.combat_formulas.random.random", return_value=0.99):
            with capture_game_messages():
                dispatch_text_command(self.player.id, "bash rat")

        self.player.refresh_from_db()
        self.assertEqual(self.player.ability_cooldowns, {"bash": 6})

    def test_state_sync_includes_ability_hotkeys_cooldowns_and_definitions(self):
        from spawns.state_payloads import build_state_sync

        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            cast_time={"rounds": 1},
            cooldown={"rounds": 2},
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self.player.known_abilities = ["power-strike"]
        self.player.ability_hotkeys = {"1": "power-strike"}
        self.player.ability_cooldowns = {"power-strike": 2}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys", "ability_cooldowns"])

        payload = build_state_sync(self.player).model_dump()

        self.assertEqual(payload["actor"]["known_abilities"], ["power-strike"])
        self.assertEqual(payload["actor"]["ability_hotkeys"], {"1": "power-strike"})
        self.assertEqual(payload["actor"]["ability_cooldowns"], {"power-strike": 2})
        self.assertEqual(
            payload["world"]["abilities"]["definitions"]["power-strike"]["cooldown"],
            {"rounds": 2},
        )
        self.assertEqual(
            payload["world"]["abilities"]["definitions"]["power-strike"]["cast_time"],
            {"rounds": 1},
        )
        self.assertTrue(
            payload["world"]["abilities"]["definitions"]["power-strike"][
                "consumes_primary_action_on_resolve"
            ]
        )
        self.assertTrue(
            payload["world"]["abilities"]["definitions"]["power-strike"][
                "consumes_primary_action_while_casting"
            ]
        )
        self.assertEqual(payload["prepared_abilities"], [])

    def test_state_sync_includes_active_prepared_abilities(self):
        from spawns.state_payloads import build_state_sync

        mob = self._mob()
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            pending_player_ability={
                "ability": "charged-strike",
                "status": "queued",
                "cast_rounds_remaining": 1,
            },
        )
        second_encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=self._mob(),
            pending_player_ability={
                "ability": "charged-mark",
                "status": "casting",
                "cast_rounds_remaining": 1,
            },
        )

        payload = build_state_sync(self.player).model_dump()

        self.assertEqual(
            payload["prepared_abilities"],
            ["charged-strike", "charged-mark"],
        )

        encounter.pending_player_ability = {
            "ability": "charged-strike",
            "command": "charged-strike",
        }
        encounter.save(update_fields=["pending_player_ability"])

        payload = build_state_sync(self.player).model_dump()

        self.assertEqual(
            payload["prepared_abilities"],
            ["charged-strike", "charged-mark"],
        )

        encounter.pending_player_ability = {}
        encounter.save(update_fields=["pending_player_ability"])

        payload = build_state_sync(self.player).model_dump()

        self.assertEqual(payload["prepared_abilities"], ["charged-mark"])

        second_encounter.pending_player_ability = {}
        second_encounter.save(update_fields=["pending_player_ability"])

        payload = build_state_sync(self.player).model_dump()

        self.assertEqual(payload["prepared_abilities"], [])

    def test_state_sync_includes_active_player_combat_effects(self):
        from spawns.state_payloads import build_state_sync

        mob = self._mob()
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
        )
        create_active_effect(
            target=self.player,
            source=mob,
            encounter=encounter,
            scope="encounter",
            payload={
                "effect": "stun",
                "source": {"type": "mob", "id": mob.id},
                "target": {"type": "player", "id": self.player.id},
                "remaining_rounds": 1,
                "duration_rounds": 1,
                "label": "Crack",
            },
        )

        payload = build_state_sync(self.player).model_dump()

        self.assertEqual(len(payload["actor"]["combat_effects"]), 1)
        self.assertEqual(payload["actor"]["combat_effects"][0]["label"], "Crack")
        self.assertEqual(
            payload["actor"]["combat_effects"][0]["encounter_id"],
            encounter.id,
        )

    def test_queued_ability_replaces_auto_attack_for_the_round(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Power Strike"},
                }
            ],
        )
        self.player.known_abilities = ["power-strike"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 5)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "strike rat")

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 3)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(len(attacks), 1)
        self.assertEqual(attacks[0]["data"]["attack"], "power-strike")
        self.assertEqual(attacks[0]["data"]["damage_taken"], self.stats["attack_power"] * 2)

    def test_immediate_resolution_prepares_then_clears_ability_state(self):
        self.world.config.combat_resolution_interval = 0
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Power Strike"},
                }
            ],
        )
        self.player.known_abilities = ["power-strike"]
        self.player.save(update_fields=["known_abilities"])
        self._mob(health=self.stats["attack_power"] * 4)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "strike rat")

        prepared = self._messages_by_type(messages, "cmd.ability.success")[0]
        self.assertEqual(
            prepared["data"]["prepared_abilities"],
            ["power-strike"],
        )
        preparation_updates = self._messages_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertEqual(
            [update["data"]["abilities"] for update in preparation_updates],
            [[]],
        )

    def test_prepare_transition_keeps_authoritative_clear_after_stale_update(self):
        stale_event = GameEvent(
            type="player.ability_preparations.update",
            recipients=[self.player.key],
            data={"abilities": ["power-strike"]},
        )
        result = CombatStepResult(
            actor_key=self.player.key,
            events=[stale_event],
            encounter_active=True,
        )

        transitioned = _with_ability_prepare_transition(
            result,
            player=self.player,
            previous_slug="power-strike",
            current_slug=None,
        )

        self.assertEqual(
            [
                event.data["abilities"]
                for event in transitioned.events
                if event.type == "player.ability_preparations.update"
            ],
            [["power-strike"], []],
        )

        already_current = CombatStepResult(
            actor_key=self.player.key,
            events=[transitioned.events[-1]],
            encounter_active=True,
        )
        deduplicated = _with_ability_prepare_transition(
            already_current,
            player=self.player,
            previous_slug="power-strike",
            current_slug=None,
        )
        self.assertEqual(deduplicated.events, already_current.events)

    def test_charge_moves_to_adjacent_room_and_attacks_with_opener_priority(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self._charge_ability()
        self.player.known_abilities = ["charge"]
        self.player.save(update_fields=["known_abilities"])
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=dest_room.id,
            event=adv_consts.TRIGGER_EVENT_ENTER,
            script="/cmd room -- /echo -- The charge horn sounds.",
            display_action_in_room=False,
            gate_delay=0,
        )
        mob = self._mob(room=dest_room, attack_power=4, fights_back=True)
        expected_damage = math.ceil(self.stats["attack_power"] * 1.5)

        with patch("spawns.actions.combat.random.randint", side_effect=[10, 20]):
            with patch("spawns.tasks.resolve_combat_encounter.apply_async") as schedule_mock:
                with capture_game_messages() as messages:
                    with self.captureOnCommitCallbacks(execute=True):
                        dispatch_text_command(self.player.id, "charge rat east")

        self.player.refresh_from_db()
        mob.refresh_from_db()
        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(self.player.room_id, dest_room.id)
        self.assertEqual(self.player.stamina, self.stats["stamina_max"] - movement_cost(dest_room))
        self.assertEqual(encounter.round_number, 1)
        self.assertEqual(encounter.resolution_interval, 1.5)
        self.assertIsNotNone(encounter.next_resolution_ts)
        self.assertEqual(encounter.initiative_order[0]["type"], "mob")
        self.assertEqual(encounter.opening_priority[0]["source"], "charge")

        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[0]["data"]["actor"]["key"], self.player.key)
        self.assertEqual(attacks[0]["data"]["attack"], "charge")
        self.assertEqual(attacks[0]["data"]["damage_taken"], expected_damage)
        self.assertEqual(attacks[1]["data"]["actor"]["key"], mob.key)
        moves = self._messages_by_type(messages, "cmd.move.success")
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["data"]["direction"], "east")
        enter_echoes = [
            entry["message"]
            for entry in messages
            if entry["message"].get("type") == "cmd./echo.success"
            and "charge horn" in entry["message"].get("text", "")
        ]
        self.assertEqual(
            len(enter_echoes),
            1,
            [entry["message"] for entry in messages],
        )

        self.assertEqual(self.player.ability_cooldowns, {"charge": 10})
        schedule_mock.assert_called_once()
        self.assertEqual(
            schedule_mock.call_args.kwargs["kwargs"]["encounter_id"],
            encounter.id,
        )
        self.assertEqual(schedule_mock.call_args.kwargs["countdown"], 1.5)

    def test_charge_revalidates_target_room_after_lock_race(self):
        self._charge_ability()
        self.player.known_abilities = ["charge"]
        self.player.save(update_fields=["known_abilities"])
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        moved_room = dest_room.create_at(adv_consts.DIRECTION_EAST)
        mob = self._mob(room=dest_room)

        def move_before_lock(*args, **kwargs):
            mob.room = moved_room
            mob.save(update_fields=["room"])
            return mob

        with patch(
            "spawns.actions.abilities.resolve_room_mob_target",
            side_effect=move_before_lock,
        ):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "charge rat east")

        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        # The simulated move shares this test transaction and is rolled back
        # with the rejected opener; the important invariant is no encounter.
        self.assertEqual(mob.room_id, dest_room.id)
        self.assertFalse(CombatEncounter.objects.filter(mob=mob).exists())
        errors = self._messages_by_type(messages, "cmd.ability.error")
        self.assertEqual(errors[0]["data"]["code"], "target_missing")

    def test_hostile_opener_revalidates_target_room_after_lock_race(self):
        self._ability(
            slug="power-strike-race",
            name="Power Strike",
            verbs=["strike-race"],
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "text": {"label": "Power Strike"},
                }
            ],
        )
        self.player.known_abilities = ["power-strike-race"]
        self.player.save(update_fields=["known_abilities"])
        moved_room = self.room.create_at(adv_consts.DIRECTION_WEST)
        mob = self._mob(room=self.room)

        def move_before_lock(*args, **kwargs):
            mob.room = moved_room
            mob.save(update_fields=["room"])
            return mob

        with patch(
            "spawns.actions.abilities.resolve_room_mob_target",
            side_effect=move_before_lock,
        ):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "strike-race rat")

        mob.refresh_from_db()
        self.assertEqual(mob.room_id, self.room.id)
        self.assertFalse(CombatEncounter.objects.filter(mob=mob).exists())
        errors = self._messages_by_type(messages, "cmd.ability.error")
        self.assertEqual(errors[0]["data"]["code"], "target_missing")

    def test_charge_accepts_direction_before_target(self):
        self._charge_ability()
        self.player.known_abilities = ["charge"]
        self.player.save(update_fields=["known_abilities"])
        dest_room = self.room.create_at(adv_consts.DIRECTION_NORTH)
        mob = self._mob(room=dest_room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "charge north rat")

        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual(self.player.room_id, dest_room.id)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[0]["data"]["actor"]["key"], self.player.key)
        self.assertEqual(attacks[0]["data"]["target"]["key"], mob.key)

    def test_charge_direction_without_target_uses_first_attackable_mob(self):
        self._charge_ability()
        self.player.known_abilities = ["charge"]
        self.player.save(update_fields=["known_abilities"])
        dest_room = self.room.create_at(adv_consts.DIRECTION_SOUTH)
        Mob.objects.create(
            world=self.spawn_world,
            room=dest_room,
            name="Blacksmith",
            keywords="blacksmith",
            health=self.stats["attack_power"] * 10,
            health_max=self.stats["attack_power"] * 10,
            attackable=False,
        )
        mob = self._mob(room=dest_room)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "charge south")

        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual(self.player.room_id, dest_room.id)
        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertTrue(encounter.faceoff_override)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[0]["data"]["actor"]["key"], self.player.key)
        self.assertEqual(attacks[0]["data"]["target"]["key"], mob.key)
        self.assertEqual(attacks[0]["data"]["attack"], "charge")

    def test_charge_target_overrides_room_aggro_target_priority(self):
        self._charge_ability()
        self.player.known_abilities = ["charge"]
        self.player.save(update_fields=["known_abilities"])
        dest_room = self.room.create_at(adv_consts.DIRECTION_SOUTH)
        archer = Mob.objects.create(
            world=self.spawn_world,
            room=dest_room,
            name="Persian Archer",
            keywords="persian archer",
            health=self.stats["attack_power"] * 10,
            health_max=self.stats["attack_power"] * 10,
            attack_power=3,
            fights_back=True,
            aggression=adv_consts.MOB_AGGRESSION_ALL,
            target_priority=-1,
        )
        archer.create_corpse()
        tank = Mob.objects.create(
            world=self.spawn_world,
            room=dest_room,
            name="Sparabara",
            keywords="sparabara",
            health=self.stats["attack_power"] * 10,
            health_max=self.stats["attack_power"] * 10,
            attack_power=3,
            fights_back=True,
            aggression=adv_consts.MOB_AGGRESSION_ALL,
            target_priority=1,
        )
        tank.create_corpse()

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "charge archer south")

        archer_encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=archer,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        tank_encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=tank,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertTrue(archer_encounter.faceoff_override)
        self.assertFalse(tank_encounter.faceoff_override)

        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertTrue(
            any(
                attack["data"]["actor"]["key"] == self.player.key
                and attack["data"]["target"]["key"] == archer.key
                and attack["data"]["attack"] == "charge"
                for attack in attacks
            )
        )
        self.assertTrue(
            any(attack["data"]["actor"]["key"] == tank.key for attack in attacks)
        )
        self.assertFalse(
            any(
                attack["data"]["actor"]["key"] == self.player.key
                and attack["data"]["target"]["key"] == tank.key
                for attack in attacks
            )
        )

        with capture_game_messages() as followup_messages:
            dispatch_text_command(self.player.id, "k")

        followup_attacks = self._messages_by_type(
            followup_messages,
            "notification.combat.attack",
        )
        self.assertTrue(
            any(
                attack["data"]["actor"]["key"] == self.player.key
                and attack["data"]["target"]["key"] == archer.key
                for attack in followup_attacks
            )
        )
        self.assertFalse(
            any(
                attack["data"]["actor"]["key"] == self.player.key
                and attack["data"]["target"]["key"] == tank.key
                for attack in followup_attacks
            )
        )

    def test_charge_can_open_current_room_combat_without_direction(self):
        self._charge_ability()
        self.player.known_abilities = ["charge"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(attack_power=4, fights_back=True)
        expected_damage = math.ceil(self.stats["attack_power"] * 1.5)

        with patch("spawns.actions.combat.random.randint", side_effect=[10, 20]):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "charge rat")

        self.player.refresh_from_db()
        mob.refresh_from_db()
        encounter = CombatEncounter.objects.get(
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(encounter.round_number, 1)
        self.assertEqual(encounter.initiative_order[0]["type"], "mob")
        self.assertEqual(encounter.opening_priority[0]["source"], "charge")
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[0]["data"]["actor"]["key"], self.player.key)
        self.assertEqual(attacks[0]["data"]["attack"], "charge")
        self.assertEqual(attacks[0]["data"]["damage_taken"], expected_damage)
        self.assertEqual(attacks[1]["data"]["actor"]["key"], mob.key)
        self.assertEqual(self._messages_by_type(messages, "cmd.move.success"), [])
        self.assertEqual(self.player.ability_cooldowns, {"charge": 10})

    def test_charge_can_only_be_used_out_of_combat(self):
        self._charge_ability()
        self.player.known_abilities = ["charge"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob()
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            status=CombatEncounter.STATUS_ACTIVE,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "charge rat")

        errors = self._messages_by_type(messages, "cmd.ability.error")
        self.assertEqual(errors[0]["data"]["code"], "combat_in_progress")

    def test_cast_time_ability_charges_one_round_before_resolving(self):
        self._ability(
            slug="charged-strike",
            name="Charged Strike",
            verbs=["charge"],
            cast_time={"rounds": 1},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Charged Strike"},
                }
            ],
        )
        self.player.known_abilities = ["charged-strike"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 5)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "charge rat")

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 5)
        casts = self._messages_by_type(messages, "notification.combat.ability_casting")
        self.assertEqual(len(casts), 1)
        self.assertEqual(casts[0]["data"]["ability"]["slug"], "charged-strike")
        self.assertEqual(casts[0]["data"]["rounds_remaining"], 0)
        prepared = self._messages_by_type(messages, "cmd.ability.success")[0]
        self.assertEqual(prepared["text"], "You prepare Charged Strike.")
        self.assertEqual(
            prepared["data"]["prepared_abilities"],
            ["charged-strike"],
        )
        preparation_updates = self._messages_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertEqual(preparation_updates, [])
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks, [])

        encounter = CombatEncounter.objects.get(player=self.player, mob=mob, status=CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(encounter.pending_player_ability["status"], "casting")
        self.assertEqual(encounter.pending_player_ability["cast_rounds_remaining"], 0)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill rat")

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 3)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(len(attacks), 1)
        self.assertEqual(attacks[0]["data"]["attack"], "charged-strike")
        self.assertEqual(attacks[0]["data"]["damage_taken"], self.stats["attack_power"] * 2)
        preparation_updates = self._messages_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertEqual(
            [update["data"]["abilities"] for update in preparation_updates],
            [[]],
        )

    def test_resolving_one_charge_preserves_other_encounter_preparation_state(self):
        for slug, name in (
            ("charged-strike", "Charged Strike"),
            ("charged-mark", "Charged Mark"),
        ):
            self._ability(
                slug=slug,
                name=name,
                verbs=[slug],
                cast_time={"rounds": 1},
                components=[
                    {
                        "type": "damage",
                        "profile": "basic_physical",
                        "text": {"label": name},
                    }
                ],
            )
        self.player.known_abilities = ["charged-strike", "charged-mark"]
        self.player.save(update_fields=["known_abilities"])
        first_mob = self._mob()
        first_encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=first_mob,
            pending_player_ability={
                "ability": "charged-strike",
                "status": "casting",
                "cast_rounds_remaining": 0,
            },
        )
        second_encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=self._mob(),
            pending_player_ability={
                "ability": "charged-mark",
                "status": "casting",
                "cast_rounds_remaining": 1,
            },
        )

        with capture_game_messages() as messages:
            resolve_combat_encounter(first_encounter.id)

        second_encounter.refresh_from_db()
        self.assertEqual(
            second_encounter.pending_player_ability["status"],
            "casting",
        )
        preparation_updates = self._messages_by_type(
            messages,
            "player.ability_preparations.update",
        )
        self.assertEqual(
            [update["data"]["abilities"] for update in preparation_updates],
            [["charged-mark"]],
        )

    def test_cast_time_can_allow_basic_attack_while_charging(self):
        self._ability(
            slug="charged-mark",
            name="Charged Mark",
            verbs=["mark"],
            cast_time={"rounds": 1},
            consumes_primary_action_on_resolve=True,
            consumes_primary_action_while_casting=False,
            components=[
                {
                    "type": "effect",
                    "effect": "stun",
                    "duration": {"rounds": 1},
                    "text": {"label": "Charged Mark"},
                }
            ],
        )
        self.player.known_abilities = ["charged-mark"]
        self.player.save(update_fields=["known_abilities"])
        starting_health = self.stats["attack_power"] * 5
        mob = self._mob(health=starting_health)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "mark rat")

        mob.refresh_from_db()
        casts = self._messages_by_type(messages, "notification.combat.ability_casting")
        player_attacks = [
            attack
            for attack in self._messages_by_type(messages, "notification.combat.attack")
            if attack["data"]["actor"]["key"] == self.player.key
        ]
        effects = self._messages_by_type(messages, "notification.combat.effect")
        self.assertEqual(len(casts), 1)
        self.assertEqual(len(player_attacks), 1)
        self.assertEqual(effects, [])
        self.assertLess(mob.health, starting_health)

    def test_charging_ability_cannot_be_replaced_mid_cast(self):
        self._ability(
            slug="charged-strike",
            name="Charged Strike",
            verbs=["charge"],
            cast_time={"rounds": 1},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 2},
                    "text": {"label": "Charged Strike"},
                }
            ],
        )
        self._ability(
            slug="quick-jab",
            name="Quick Jab",
            verbs=["jab"],
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 1},
                    "text": {"label": "Quick Jab"},
                }
            ],
        )
        self.player.known_abilities = ["charged-strike", "quick-jab"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 5)

        dispatch_text_command(self.player.id, "charge rat")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "jab rat")

        errors = self._messages_by_type(messages, "cmd.ability.error")
        self.assertEqual(errors[0]["data"]["code"], "ability_cast_in_progress")
        encounter = CombatEncounter.objects.get(player=self.player, mob=mob, status=CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(encounter.pending_player_ability["ability"], "charged-strike")

    def test_queued_ability_can_be_replaced_before_scheduled_resolution(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            cast_time={"rounds": 1},
            components=[{"type": "damage", "profile": "basic_physical", "overrides": {"multiplier": 2}, "text": {"label": "Power Strike"}}],
        )
        self._ability(
            slug="quick-jab",
            name="Quick Jab",
            verbs=["jab"],
            components=[{"type": "damage", "profile": "basic_physical", "overrides": {"multiplier": 1}, "text": {"label": "Quick Jab"}}],
        )
        self.player.known_abilities = ["power-strike", "quick-jab"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 5)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as prepared_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    dispatch_text_command(self.player.id, "strike rat")
            encounter = CombatEncounter.objects.get(
                player=self.player,
                mob=mob,
                status=CombatEncounter.STATUS_ACTIVE,
            )
            self.assertEqual(encounter.pending_player_ability["status"], "queued")
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "jab rat")

        encounter.refresh_from_db()
        self.assertEqual(encounter.pending_player_ability["ability"], "quick-jab")
        prepared = self._messages_by_type(
            prepared_messages,
            "cmd.ability.success",
        )[0]
        self.assertEqual(prepared["text"], "You prepare Power Strike.")
        self.assertEqual(
            prepared["data"]["prepared_abilities"],
            ["power-strike"],
        )
        switched = self._messages_by_type(messages, "cmd.ability.success")[0]
        self.assertEqual(switched["text"], "You switch to Quick Jab.")
        self.assertEqual(
            switched["data"]["prepared_abilities"],
            ["quick-jab"],
        )
        encounter.next_resolution_ts = timezone.now()
        encounter.save(update_fields=["next_resolution_ts"])

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            resolve_combat_encounter(encounter.id)

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 4)

    def test_ability_can_build_and_spend_character_state(self):
        self._ability(
            slug="quick-jab",
            name="Quick Jab",
            verbs=["jab"],
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 1},
                    "text": {"label": "Quick Jab"},
                },
                {
                    "type": "state",
                    "scope": "character",
                    "key": "combo_points",
                    "op": "increment",
                    "amount": 1,
                    "max": 5,
                    "apply": "on_hit",
                },
            ],
        )
        self._ability(
            slug="finisher",
            name="Finisher",
            verbs=["finish"],
            requirements={"gte": ["state.character.combo_points", 1]},
            components=[
                {
                    "type": "damage",
                    "profile": "basic_physical",
                    "overrides": {"multiplier": 1},
                    "scaling": {
                        "from": "state.character.combo_points",
                        "multiplier_per_point": 1,
                    },
                    "text": {"label": "Finisher"},
                },
                {
                    "type": "state",
                    "scope": "character",
                    "key": "combo_points",
                    "op": "clear",
                },
            ],
        )
        self.player.known_abilities = ["quick-jab", "finisher"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 10)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "jab rat")

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 9)
        self.assertEqual(
            get_state_value(STATE_SCOPE_CHARACTER, self.player, "combo_points"),
            1,
        )
        state_updates = self._messages_by_type(messages, "notification.ability.state")
        self.assertEqual(state_updates[-1]["data"]["value"], 1)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "finish")

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 7)
        self.assertIsNone(
            get_state_value(STATE_SCOPE_CHARACTER, self.player, "combo_points")
        )
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[0]["data"]["damage_taken"], self.stats["attack_power"] * 2)
        state_updates = self._messages_by_type(messages, "notification.ability.state")
        self.assertTrue(state_updates[-1]["data"]["cleared"])

    def test_invalid_queued_ability_falls_back_to_auto_attack(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self._ability(
            slug="energy-strike",
            name="Energy Strike",
            verbs=["mstrike"],
            cost={"resource": "energy", "amount": 1, "calc": "fixed"},
            components=[{"type": "damage", "profile": "basic_physical", "overrides": {"multiplier": 2}, "text": {"label": "Energy Strike"}}],
        )
        self.player.known_abilities = ["energy-strike"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 5)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "mstrike rat")

        self.player.energy = 0
        self.player.save(update_fields=["energy"])
        encounter = CombatEncounter.objects.get(player=self.player, mob=mob, status=CombatEncounter.STATUS_ACTIVE)
        encounter.next_resolution_ts = timezone.now()
        encounter.save(update_fields=["next_resolution_ts"])

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                resolve_combat_encounter(encounter.id)

        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 4)
        failures = self._messages_by_type(messages, "notification.combat.ability_failed")
        self.assertEqual(len(failures), 1)

    def test_stun_prevents_mob_counterattack(self):
        self._ability(
            slug="stun-bash",
            name="Stun Bash",
            verbs=["bash"],
            components=[
                {
                    "type": "effect",
                    "effect": "stun",
                    "duration": {"rounds": 1},
                    "apply": "on_resolve",
                    "text": {"label": "Stun Bash"},
                }
            ],
        )
        self.player.known_abilities = ["stun-bash"]
        self.player.save(update_fields=["known_abilities"])
        self._mob(attack_power=9, fights_back=True)

        with patch("spawns.actions.combat.random.randint", side_effect=[20, 10]):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "bash rat")

        self.player.refresh_from_db()
        self.assertEqual(self.player.health, self.stats["health_max"])
        effect_messages = self._messages_by_type(messages, "notification.combat.effect")
        self.assertTrue(any("stunned" in msg["text"] for msg in effect_messages))

    def test_combat_effect_update_includes_stunned_mob_target(self):
        self._ability(
            slug="stun-bash",
            name="Stun Bash",
            verbs=["bash"],
            components=[
                {
                    "type": "effect",
                    "effect": "stun",
                    "duration": {"rounds": 2},
                    "apply": "on_resolve",
                    "text": {"label": "Stun Bash"},
                }
            ],
        )
        self.player.known_abilities = ["stun-bash"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(attack_power=9, fights_back=True)

        with patch("spawns.actions.combat.random.randint", side_effect=[20, 10]):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "bash rat")

        updates = self._messages_by_type(
            messages,
            "player.combat_effects.update",
        )
        mob_state = next(
            combatant
            for combatant in updates[-1]["data"]["combatants"]
            if combatant["target"]["key"] == mob.key
        )

        self.assertEqual(len(mob_state["active_effects"]), 1)
        self.assertEqual(mob_state["active_effects"][0]["effect"], "stun")
        self.assertEqual(mob_state["active_effects"][0]["remaining_rounds"], 1)
        self.assertEqual(mob_state["active_effects"][0]["duration_rounds"], 2)

    def test_combatant_effect_snapshot_uses_one_query_for_both_sides(self):
        from spawns.actions.effects import active_combatant_effects

        mob = self._mob()
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
        )
        create_active_effect(
            target=self.player,
            source=mob,
            encounter=encounter,
            scope="encounter",
            payload={
                "effect": "dot",
                "label": "Wound",
                "remaining_rounds": 2,
            },
        )
        create_active_effect(
            target=mob,
            source=self.player,
            encounter=encounter,
            scope="encounter",
            payload={
                "effect": "stun",
                "label": "Stun Bash",
                "remaining_rounds": 1,
            },
        )

        with self.assertNumQueries(1):
            effects_by_key = active_combatant_effects([self.player, mob])

        self.assertEqual(effects_by_key[self.player.key][0]["effect"], "dot")
        self.assertEqual(effects_by_key[mob.key][0]["effect"], "stun")

    def test_dot_application_reports_actor_target_and_room_text(self):
        self._ability(
            slug="wound",
            name="Wound",
            verbs=["wound"],
            components=[
                {
                    "type": "effect",
                    "effect": "dot",
                    "duration": {"rounds": 2},
                    "tick": {
                        "every_rounds": 1,
                        "component": {
                            "type": "damage",
                            "profile": "basic_physical",
                            "overrides": {"multiplier": 1},
                            "text": {"label": "Wound"},
                        },
                    },
                    "apply": "on_resolve",
                    "text": {"label": "Wound"},
                }
            ],
        )
        self.player.known_abilities = ["wound"]
        self.player.save(update_fields=["known_abilities"])
        watcher = self.create_player(
            "Watcher",
            user=self.create_user("watcher@example.com"),
            room=self.room,
        )
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])
        mob = self._mob()

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "wound rat")

        actor_effects = self._messages_by_type(messages, "notification.combat.effect")
        actor_effect = next(msg for msg in actor_effects if msg["data"]["label"] == "Wound")
        watcher_effect = next(
            msg["message"]
            for msg in messages
            if msg["player_key"] == watcher.key
            and msg["message"].get("type") == "notification.combat.effect"
            and msg["message"]["data"].get("label") == "Wound"
        )
        self.assertEqual(actor_effect["text"], "You apply Wound on Rat.")
        self.assertEqual(watcher_effect["text"], "Joe applies Wound on Rat.")
        self.assertEqual(actor_effect["data"]["actor"]["key"], self.player.key)
        self.assertEqual(actor_effect["data"]["target"]["key"], mob.key)
        self.assertEqual(actor_effect["data"]["effect"], "dot")

    def test_mob_dot_application_reports_target_and_room_text(self):
        self._ability(
            slug="wound",
            name="Wound",
            verbs=["wound"],
            components=[
                {
                    "type": "effect",
                    "effect": "dot",
                    "duration": {"rounds": 2},
                    "tick": {
                        "every_rounds": 1,
                        "component": {
                            "type": "damage",
                            "profile": "basic_physical",
                            "overrides": {"multiplier": 1},
                            "text": {"label": "Wound"},
                        },
                    },
                    "apply": "on_resolve",
                    "text": {"label": "Wound"},
                }
            ],
        )
        watcher = self.create_player(
            "Watcher",
            user=self.create_user("watcher@example.com"),
            room=self.room,
        )
        watcher.in_game = True
        watcher.save(update_fields=["in_game"])
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="cave-shaman",
            name="a cave shaman",
            keywords="cave shaman",
            base_properties={
                "level": 1,
                "health_max": 200,
                "attack_power": 7,
                "weapon_damage": 0,
                "fights_back": True,
            },
            combat_abilities=[
                {
                    "ability": "wound",
                    "weight": 1,
                }
            ],
        )
        mob = mob_definition.spawn(self.room, self.spawn_world)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with patch("spawns.actions.combat.random.randint", return_value=1):
                with capture_game_messages() as messages:
                    resolve_combat_encounter(encounter.id)

        target_effects = self._messages_by_type(messages, "notification.combat.effect")
        target_effect = next(msg for msg in target_effects if msg["data"]["label"] == "Wound")
        watcher_effect = next(
            msg["message"]
            for msg in messages
            if msg["player_key"] == watcher.key
            and msg["message"].get("type") == "notification.combat.effect"
            and msg["message"]["data"].get("label") == "Wound"
        )
        self.assertEqual(target_effect["text"], "A cave shaman applies Wound on you.")
        self.assertEqual(watcher_effect["text"], "A cave shaman applies Wound on Joe.")
        self.assertEqual(target_effect["data"]["actor"]["key"], mob.key)
        self.assertEqual(target_effect["data"]["target"]["key"], self.player.key)
        self.assertEqual(target_effect["data"]["effect"], "dot")

    def test_dot_ticks_during_following_encounter_rounds(self):
        self._ability(
            slug="bleeding-cut",
            name="Bleeding Cut",
            verbs=["bleed"],
            components=[
                {
                    "type": "effect",
                    "effect": "dot",
                    "duration": {"rounds": 2},
                    "tick": {
                        "every_rounds": 1,
                        "component": {
                            "type": "damage",
                            "profile": "basic_physical",
                            "overrides": {"multiplier": 1},
                            "text": {"label": "Bleed"},
                        },
                    },
                    "apply": "on_resolve",
                }
            ],
        )
        self.player.known_abilities = ["bleeding-cut"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 6)

        dispatch_text_command(self.player.id, "bleed rat")
        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 6)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill rat")
        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 4)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        dot_attack = next(msg for msg in attacks if msg["data"]["label"] == "Bleed")
        self.assertEqual(
            dot_attack["text"],
            f"Rat suffers {dot_attack['data']['damage_taken']} damage from your Bleed.",
        )

    def test_dot_tick_against_player_uses_passive_recipient_text(self):
        self.player.name = "Hoplite"
        self.player.save(update_fields=["name"])
        mob = self._mob(
            health=self.stats["attack_power"] * 6,
            attack_power=self.stats["attack_power"],
            fights_back=False,
        )
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )
        create_active_effect(
            target=self.player,
            source=mob,
            encounter=encounter,
            payload={
                "effect": "dot",
                "category": "debuff",
                "source": {"type": "mob", "id": mob.id},
                "target": {"type": "player", "id": self.player.id},
                "remaining_rounds": 1,
                "rounds_elapsed": 0,
                "label": "Venom",
                "primitives": [],
                "tick": {
                    "every_rounds": 1,
                    "component": {
                        "type": "damage",
                        "profile": "basic_physical",
                        "overrides": {"multiplier": 1},
                        "text": {"label": "Venom"},
                    },
                },
            },
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                resolve_combat_encounter(encounter.id)

        attacks = self._messages_by_type(messages, "notification.combat.attack")
        dot_attack = next(msg for msg in attacks if msg["data"]["label"] == "Venom")
        self.assertEqual(
            dot_attack["text"],
            f"You suffer {dot_attack['data']['damage_taken']} damage from Rat's Venom.",
        )

    def test_dot_tick_from_another_player_uses_source_possessive(self):
        from spawns.actions.combat import _periodic_damage_text

        source = Player.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Hoplite",
            user=self.create_user("hoplite@example.com"),
        )
        result = CombatAttackResult(
            profile="basic_physical",
            damage_type="physical",
            outcome="hit",
            damage_base=16,
            damage_dealt=16,
            damage_taken=16,
            damage_mitigated=0,
            damage_absorbed=0,
            healing_done=0,
            is_crit_hit=False,
            is_heal=False,
            dodge_chance=0,
            crit_chance=0,
            armor_mitigation=0,
            resilience_mitigation=0,
        )

        self.assertEqual(
            _periodic_damage_text(
                viewer=self.player,
                source=source,
                target=self.player,
                label="Wound",
                result=result,
            ),
            "You suffer 16 damage from Hoplite's Wound.",
        )

    def test_non_consuming_dot_allows_auto_attack_in_application_round(self):
        self._ability(
            slug="bleeding-cut",
            name="Bleeding Cut",
            verbs=["bleed"],
            consumes_primary_action_on_resolve=False,
            components=[
                {
                    "type": "effect",
                    "effect": "dot",
                    "duration": {"rounds": 2},
                    "tick": {
                        "every_rounds": 1,
                        "component": {
                            "type": "damage",
                            "profile": "basic_physical",
                            "overrides": {"multiplier": 1},
                            "text": {"label": "Bleed"},
                        },
                    },
                    "apply": "on_resolve",
                }
            ],
        )
        self.player.known_abilities = ["bleeding-cut"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 6)

        dispatch_text_command(self.player.id, "bleed rat")
        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 5)

        dispatch_text_command(self.player.id, "kill rat")
        mob.refresh_from_db()
        self.assertEqual(mob.health, self.stats["attack_power"] * 3)

    def test_cleave_effect_adds_secondary_strike_without_hitting_main_twice(self):
        self._cleave_ability()
        self.player.known_abilities = ["cleave"]
        self.player.save(update_fields=["known_abilities"])
        main = self._mob(health=self.stats["attack_power"] * 5)
        secondary = self._mob(health=self.stats["attack_power"] * 5)
        secondary.name = "Bat"
        secondary.keywords = "bat"
        secondary.save(update_fields=["name", "keywords"])
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=main,
            resolution_interval=-1,
            initiative_order=self._player_first_initiative(main),
        )
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=secondary,
            resolution_interval=-1,
            initiative_order=self._player_first_initiative(secondary),
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "cleave rat")

        main.refresh_from_db()
        secondary.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(main.health, self.stats["attack_power"] * 4)
        self.assertEqual(secondary.health, self.stats["attack_power"] * 4)
        self.assertEqual(self.player.active_effects, [])
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        cleave_attacks = [
            attack for attack in attacks if attack["data"]["attack"] == "cleave"
        ]
        basic_attacks = [
            attack for attack in attacks if attack["data"]["attack"] == "attack"
        ]
        self.assertEqual(cleave_attacks[0]["data"]["target"]["key"], secondary.key)
        self.assertEqual(cleave_attacks[0]["data"]["damage_taken"], self.stats["attack_power"])
        self.assertEqual(basic_attacks[0]["data"]["target"]["key"], main.key)
        self.assertEqual(len(cleave_attacks), 1)
        self.assertEqual(len(basic_attacks), 1)

    def test_cleave_effect_does_not_add_strike_without_secondary_target(self):
        self._cleave_ability()
        self.player.known_abilities = ["cleave"]
        self.player.save(update_fields=["known_abilities"])
        main = self._mob(health=self.stats["attack_power"] * 5)
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=main,
            resolution_interval=-1,
            initiative_order=self._player_first_initiative(main),
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "cleave rat")

        main.refresh_from_db()
        self.assertEqual(main.health, self.stats["attack_power"] * 4)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(
            [attack["data"]["attack"] for attack in attacks],
            ["attack"],
        )

    def test_cleave_effect_duration_can_span_additional_rounds(self):
        self._cleave_ability(duration_rounds=2)
        self.player.known_abilities = ["cleave"]
        self.player.save(update_fields=["known_abilities"])
        main = self._mob(health=self.stats["attack_power"] * 6)
        secondary = self._mob(health=self.stats["attack_power"] * 6)
        secondary.name = "Bat"
        secondary.keywords = "bat"
        secondary.save(update_fields=["name", "keywords"])
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=main,
            resolution_interval=-1,
            initiative_order=self._player_first_initiative(main),
        )
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=secondary,
            resolution_interval=-1,
            initiative_order=self._player_first_initiative(secondary),
        )

        dispatch_text_command(self.player.id, "cleave rat")
        self.player.refresh_from_db()
        self.assertEqual(self.player.active_effects[0]["remaining_rounds"], 1)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "kill rat")

        main.refresh_from_db()
        secondary.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(main.health, self.stats["attack_power"] * 4)
        self.assertEqual(secondary.health, self.stats["attack_power"] * 4)
        self.assertEqual(self.player.active_effects, [])
        cleave_attacks = [
            attack
            for attack in self._messages_by_type(messages, "notification.combat.attack")
            if attack["data"]["attack"] == "cleave"
        ]
        self.assertEqual(cleave_attacks[0]["data"]["target"]["key"], secondary.key)

    def test_cleave_effect_can_defeat_secondary_target(self):
        self._cleave_ability()
        self.player.known_abilities = ["cleave"]
        self.player.save(update_fields=["known_abilities"])
        main = self._mob(health=self.stats["attack_power"] * 5)
        secondary = self._mob(health=self.stats["attack_power"], fights_back=False)
        secondary.name = "Bat"
        secondary.keywords = "bat"
        secondary.save(update_fields=["name", "keywords"])
        CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=main,
            resolution_interval=-1,
            initiative_order=self._player_first_initiative(main),
        )
        secondary_encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=secondary,
            resolution_interval=-1,
            initiative_order=self._player_first_initiative(secondary),
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "cleave rat")

        self.assertFalse(Mob.objects.filter(pk=secondary.pk).exists())
        self.assertEqual(
            CombatEncounter.objects.get(pk=secondary_encounter.pk).status,
            CombatEncounter.STATUS_FINISHED,
        )
        deaths = self._messages_by_type(messages, "notification.death")
        self.assertEqual(deaths[0]["data"]["deceased"]["key"], secondary.key)

    def test_resource_change_effect_ticks_energy_during_following_rounds(self):
        self._ability(
            slug="focus-renewal",
            name="Focus Renewal",
            verbs=["renewfocus"],
            target={"type": "self", "default": "self", "allow_out_of_combat": False},
            components=[
                {
                    "type": "effect",
                    "effect": "focus-renewal",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 2},
                    "tick": {
                        "every_rounds": 1,
                        "primitives": [
                            {
                                "type": "resource_change",
                                "resource": "energy",
                                "amount": 3,
                                "calc": "fixed",
                                "target": "effect.target",
                            }
                        ],
                    },
                    "apply": "on_resolve",
                }
            ],
        )
        self.player.known_abilities = ["focus-renewal"]
        self.player.energy = 0
        self.player.save(update_fields=["known_abilities", "energy"])
        self._mob(health=self.stats["attack_power"] * 20)

        dispatch_text_command(self.player.id, "kill rat")
        dispatch_text_command(self.player.id, "renewfocus")

        self.player.refresh_from_db()
        self.assertEqual(self.player.energy, 0)

        dispatch_text_command(self.player.id, "kill rat")
        self.player.refresh_from_db()
        self.assertEqual(self.player.energy, 3)

        dispatch_text_command(self.player.id, "kill rat")
        self.player.refresh_from_db()
        self.assertEqual(self.player.energy, 6)

        dispatch_text_command(self.player.id, "kill rat")
        self.player.refresh_from_db()
        self.assertEqual(self.player.energy, 6)

    def test_self_buff_can_restore_energy_when_physical_attacks_hit(self):
        self._ability(
            slug="energized-strikes",
            name="Energized Strikes",
            verbs=["energize"],
            target={"type": "self", "default": "self", "allow_out_of_combat": False},
            components=[
                {
                    "type": "effect",
                    "effect": "energized-strikes",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 2},
                    "primitives": [
                        {
                            "type": "proc",
                            "phase": "after_damage",
                            "conditions": {
                                "all": [
                                    {"eq": ["event.actor", "{effect.target}"]},
                                    {"eq": ["event.attack", "attack"]},
                                    {"eq": ["event.damage_type", "physical"]},
                                    {"gte": ["event.damage_taken", 1]},
                                ]
                            },
                            "actions": [
                                {
                                    "type": "resource_change",
                                    "resource": "energy",
                                    "amount": 3,
                                    "calc": "fixed",
                                    "target": "effect.target",
                                }
                            ],
                        }
                    ],
                    "apply": "on_resolve",
                }
            ],
        )
        self.player.known_abilities = ["energized-strikes"]
        self.player.energy = 0
        self.player.save(update_fields=["known_abilities", "energy"])
        self._mob(health=self.stats["attack_power"] * 20)

        dispatch_text_command(self.player.id, "kill rat")
        dispatch_text_command(self.player.id, "energize")

        self.player.refresh_from_db()
        self.assertEqual(self.player.energy, 0)

        dispatch_text_command(self.player.id, "kill rat")
        self.player.refresh_from_db()
        self.assertEqual(self.player.energy, 3)

        dispatch_text_command(self.player.id, "kill rat")
        self.player.refresh_from_db()
        self.assertEqual(self.player.energy, 6)

        dispatch_text_command(self.player.id, "kill rat")
        self.player.refresh_from_db()
        self.assertEqual(self.player.energy, 6)

    def test_room_damage_buff_refreshes_without_stacking_across_casters(self):
        self._shout_ability()
        ally = self.create_player(
            "Ally",
            user=self.create_user("ally@example.com"),
            room=self.room,
        )
        ally.in_game = True
        ally.known_abilities = ["shout"]
        ally.save(update_fields=["in_game", "known_abilities"])
        self.player.known_abilities = ["shout"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 20)

        baseline = resolve_attack(
            actor=self.player,
            target=mob,
            world=self.player.world,
            profile_key="basic_physical",
        ).damage_taken

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "shout")
        self.player.refresh_from_db()
        ally.refresh_from_db()

        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )
        self.assertEqual(len(self.player.active_effects), 1)
        self.assertEqual(len(ally.active_effects), 1)
        self.assertEqual(self.player.active_effects[0]["remaining_rounds"], 4)
        self.assertEqual(ally.active_effects[0]["remaining_rounds"], 4)
        self_effect_messages = self._messages_by_type(messages, "notification.ability.effect")
        self.assertEqual(
            self_effect_messages[-1]["data"]["active_effects"][0]["remaining_rounds"],
            4,
        )
        ally_effect_messages = [
            msg["message"]
            for msg in messages
            if msg["player_key"] == ally.key
            and msg["message"].get("type") == "notification.ability.effect"
        ]
        self.assertEqual(
            ally_effect_messages[-1]["data"]["active_effects"][0]["remaining_rounds"],
            4,
        )

        buffed = resolve_attack(
            actor=self.player,
            target=mob,
            world=self.player.world,
            profile_key="basic_physical",
        ).damage_taken
        self.assertEqual(buffed, math.ceil(baseline * 1.2))

        self.player.active_effect_records.update(remaining_rounds=2)
        ally.active_effect_records.update(remaining_rounds=2)

        with capture_game_messages() as messages:
            dispatch_text_command(ally.id, "shout")
        self.player.refresh_from_db()
        ally.refresh_from_db()

        self.assertEqual(len(self.player.active_effects), 1)
        self.assertEqual(len(ally.active_effects), 1)
        self.assertEqual(self.player.active_effects[0]["remaining_rounds"], 4)
        self.assertEqual(ally.active_effects[0]["remaining_rounds"], 4)
        self.assertEqual(self.player.active_effects[0]["source"]["id"], ally.id)
        self.assertEqual(ally.active_effects[0]["source"]["id"], ally.id)
        refreshed_messages = self._messages_by_type(messages, "notification.ability.effect")
        self.assertEqual(refreshed_messages[-1]["data"]["action"], "refreshed")
        self.assertEqual(
            refreshed_messages[-1]["data"]["active_effects"][0]["source"]["id"],
            ally.id,
        )

        refreshed = resolve_attack(
            actor=self.player,
            target=mob,
            world=self.player.world,
            profile_key="basic_physical",
        ).damage_taken
        self.assertEqual(refreshed, buffed)

    def test_character_damage_buff_duration_advances_in_combat_rounds(self):
        mob = self._mob(health=self.stats["attack_power"] * 20, fights_back=False)
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
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        self.assertEqual(self.player.active_effects[0]["remaining_rounds"], 1)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        self.assertEqual(self.player.active_effects, [])

    def test_character_stat_modifier_adds_fixed_armor_to_effective_stats(self):
        replace_active_effects(target=self.player, source=self.player, payloads=[
            {
                "effect": "guard",
                "category": "buff",
                "scope": "character",
                "source": {"type": "player", "id": self.player.id},
                "target": {"type": "player", "id": self.player.id},
                "remaining_rounds": 2,
                "duration_rounds": 2,
                "rounds_elapsed": 0,
                "label": "Guard",
                "primitives": [
                    {
                        "type": "stat_modifier",
                        "stat": "armor",
                        "op": "add",
                        "amount": 25,
                    }
                ],
            }
        ])

        stats = compute_stats(
            self.player.level,
            self.player.archetype,
            char=self.player,
            world=self.player.world,
        )

        self.assertEqual(stats["armor"], self.stats["armor"] + 25)

    def test_self_stat_modifier_multiplies_armor_for_incoming_physical_damage(self):
        stat_system = deepcopy(BASIC_TEST_STAT_SYSTEM)
        stat_system["formulas"]["base_stats"] = {"armor": 60}
        combat_system = deepcopy(self.world.config.combat_system)
        combat_system["profiles"]["basic_physical"]["mitigation"]["armor"] = True
        self.world.config.stat_system = stat_system
        self.world.config.combat_system = normalize_combat_system(combat_system)
        self.world.config.save(update_fields=["stat_system", "combat_system"])

        self._ability(
            slug="shield-wall",
            name="Shield Wall",
            verbs=["shieldwall"],
            target={"type": "self", "default": "self", "allow_out_of_combat": False},
            components=[
                {
                    "type": "effect",
                    "effect": "shield-wall",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "primitives": [
                        {
                            "type": "stat_modifier",
                            "stat": "armor",
                            "op": "multiply",
                            "multiplier": 3,
                        }
                    ],
                    "apply": "on_resolve",
                    "text": {"label": "Shield Wall"},
                }
            ],
        )
        self.player.known_abilities = ["shield-wall"]
        self.player.health = 1000
        self.player.save(update_fields=["known_abilities", "health"])
        mob = self._mob(health=1000, attack_power=100, fights_back=True)
        unbuffed = resolve_attack(
            actor=mob,
            target=self.player,
            world=self.player.world,
            profile_key="basic_physical",
            rng=lambda: 0.99,
        )
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "shieldwall")
                resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(self.player.active_effects[0]["effect"], "shield-wall")
        self.assertLess(attacks[-1]["data"]["damage_taken"], unbuffed.damage_taken)
        self.assertGreater(
            attacks[-1]["data"]["armor_mitigation"],
            unbuffed.armor_mitigation,
        )

    def test_self_barrier_applies_outside_combat_with_initialized_pool(self):
        self.player.attributes = {
            **(self.player.attributes or {}),
            "focus": 30,
        }
        self.player.save(update_fields=["attributes"])
        crest_stats = compute_stats(
            self.player.level,
            self.player.archetype,
            char=self.player,
            world=self.player.world,
        )
        self._ability(
            slug="crest",
            name="Crest",
            verbs=["crest"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            cooldown={"rounds": 3},
            consumes_primary_action_on_resolve=False,
            components=[
                {
                    "type": "effect",
                    "effect": "crest",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "text": {"label": "Crest"},
                    "primitives": [
                        {
                            "type": "damage_absorb",
                            "scaling": [
                                {"source": "ability_power", "multiplier": 0.3},
                            ],
                            "damage_types": ["physical", "ability"],
                        }
                    ],
                }
            ],
        )
        self.player.known_abilities = ["crest"]
        self.player.save(update_fields=["known_abilities"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "crest")

        self.player.refresh_from_db()
        expected_absorb = math.ceil(crest_stats["ability_power"] * 0.3)
        self.assertGreater(expected_absorb, 1)
        effect = ActiveEffect.objects.get(
            target_player=self.player,
            effect="crest",
        )
        self.assertEqual(effect.scope, ActiveEffect.SCOPE_CHARACTER)
        self.assertEqual(effect.category, "buff")
        self.assertEqual(effect.remaining_rounds, 3)
        self.assertEqual(effect.duration_rounds, 3)
        self.assertEqual(effect.primitives[0]["remaining"], expected_absorb)
        self.assertEqual(self.player.ability_cooldowns, {"crest": 3})
        self.assertFalse(
            CombatEncounter.objects.filter(
                player=self.player,
                status=CombatEncounter.STATUS_ACTIVE,
            ).exists()
        )

        effect_messages = self._messages_by_type(messages, "notification.ability.effect")
        self.assertEqual(len(effect_messages), 1)
        payload = effect_messages[0]["data"]["active_effects"][0]
        self.assertEqual(payload["label"], "Crest")
        self.assertEqual(payload["remaining_rounds"], 3)
        self.assertEqual(payload["duration_rounds"], 3)
        self.assertEqual(payload["primitives"][0]["remaining"], expected_absorb)

        mob = self._mob(
            health=10_000,
            attack_power=expected_absorb - 1,
            fights_back=True,
        )
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )
        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as combat_messages:
                resolve_combat_encounter(encounter.id)

        effect.refresh_from_db()
        self.assertEqual(effect.primitives[0]["remaining"], 1)
        self.assertEqual(effect.remaining_rounds, 2)
        effect_updates = self._messages_by_type(
            combat_messages,
            "player.combat_effects.update",
        )
        player_snapshot = next(
            combatant
            for combatant in effect_updates[-1]["data"]["combatants"]
            if combatant["target"]["key"] == self.player.key
        )
        crest_snapshot = player_snapshot["active_effects"][0]
        self.assertEqual(crest_snapshot["effect"], "crest")
        self.assertEqual(crest_snapshot["remaining_rounds"], 2)
        self.assertEqual(crest_snapshot["primitives"][0]["remaining"], 1)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as depletion_messages:
                resolve_combat_encounter(encounter.id)

        self.assertFalse(
            ActiveEffect.objects.filter(
                target_player=self.player,
                effect="crest",
            ).exists()
        )
        depleted_updates = self._messages_by_type(
            depletion_messages,
            "player.combat_effects.update",
        )
        player_snapshot = next(
            combatant
            for combatant in depleted_updates[-1]["data"]["combatants"]
            if combatant["target"]["key"] == self.player.key
        )
        self.assertEqual(player_snapshot["active_effects"], [])

    def test_explicit_encounter_barrier_rejects_out_of_combat_without_cooldown(self):
        self._ability(
            slug="battle-crest",
            name="Battle Crest",
            verbs=["battlecrest"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            cooldown={"rounds": 3},
            components=[
                {
                    "type": "effect",
                    "effect": "battle-crest",
                    "scope": "encounter",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "primitives": [
                        {
                            "type": "damage_absorb",
                            "amount": 10,
                            "calc": "fixed",
                        }
                    ],
                }
            ],
        )
        self.player.known_abilities = ["battle-crest"]
        self.player.save(update_fields=["known_abilities"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "battlecrest")

        self.player.refresh_from_db()
        errors = self._messages_by_type(messages, "cmd.ability.error")
        self.assertEqual(errors[0]["data"]["code"], "combat_required")
        self.assertEqual(self.player.ability_cooldowns, {})
        self.assertFalse(
            ActiveEffect.objects.filter(
                target_player=self.player,
                effect="battle-crest",
            ).exists()
        )

    def test_out_of_combat_percent_max_barrier_freezes_effective_health_pool(self):
        self._ability(
            slug="aegis",
            name="Aegis",
            verbs=["aegis"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            components=[
                {
                    "type": "effect",
                    "effect": "aegis",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "primitives": [
                        {
                            "type": "damage_absorb",
                            "amount": 25,
                            "calc": "percent_max",
                        }
                    ],
                }
            ],
        )
        self.player.known_abilities = ["aegis"]
        self.player.save(update_fields=["known_abilities"])
        expected_absorb = math.ceil(self.stats["health_max"] * 0.25)

        dispatch_text_command(self.player.id, "aegis")

        effect = ActiveEffect.objects.get(
            target_player=self.player,
            effect="aegis",
        )
        self.assertEqual(effect.primitives[0]["remaining"], expected_absorb)
        self.player.attributes = {
            **(self.player.attributes or {}),
            "grit": 100,
        }
        self.player.save(update_fields=["attributes"])
        effect.refresh_from_db()
        self.assertEqual(effect.primitives[0]["remaining"], expected_absorb)

    def test_zero_capacity_barrier_does_not_persist(self):
        self._ability(
            slug="empty-ward",
            name="Empty Ward",
            verbs=["emptyward"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            components=[
                {
                    "type": "effect",
                    "effect": "empty-ward",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "primitives": [
                        {
                            "type": "damage_absorb",
                            "amount": 0,
                            "calc": "fixed",
                        }
                    ],
                }
            ],
        )
        self.player.known_abilities = ["empty-ward"]
        self.player.save(update_fields=["known_abilities"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "emptyward")

        self.assertFalse(
            ActiveEffect.objects.filter(
                target_player=self.player,
                effect="empty-ward",
            ).exists()
        )
        effect_messages = self._messages_by_type(messages, "notification.ability.effect")
        self.assertEqual(effect_messages[0]["data"]["active_effects"], [])

    def test_self_barrier_absorbs_incoming_physical_damage_until_depleted(self):
        self._ability(
            slug="ward",
            name="Ward",
            verbs=["ward"],
            target={"type": "self", "default": "self", "allow_out_of_combat": False},
            components=[
                {
                    "type": "effect",
                    "effect": "ward",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "primitives": [
                        {
                            "type": "damage_absorb",
                            "amount": 10,
                            "calc": "fixed",
                            "damage_types": ["physical"],
                        }
                    ],
                    "apply": "on_resolve",
                }
            ],
        )
        self.player.known_abilities = ["ward"]
        self.player.health = 50
        self.player.save(update_fields=["known_abilities", "health"])
        mob = self._mob(health=self.stats["attack_power"] * 20, attack_power=7, fights_back=True)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "ward")
                resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(self.player.health, 50)
        self.assertEqual(encounter.active_effects[0]["primitives"][0]["remaining"], 3)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[-1]["data"]["damage_taken"], 0)
        self.assertEqual(attacks[-1]["data"]["damage_absorbed"], 7)

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(self.player.health, 46)
        self.assertEqual(encounter.active_effects, [])
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[-1]["data"]["damage_taken"], 4)
        self.assertEqual(attacks[-1]["data"]["damage_absorbed"], 3)

    def test_barrier_with_trailing_zero_pool_is_deleted_when_spent(self):
        self._ability(
            slug="layered-ward",
            name="Layered Ward",
            verbs=["layeredward"],
            target={"type": "self", "default": "self", "allow_out_of_combat": False},
            components=[
                {
                    "type": "effect",
                    "effect": "layered-ward",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "primitives": [
                        {
                            "type": "damage_absorb",
                            "amount": 5,
                            "calc": "fixed",
                            "damage_types": ["physical"],
                        },
                        {
                            "type": "damage_absorb",
                            "amount": 0,
                            "calc": "fixed",
                            "damage_types": ["physical"],
                        },
                    ],
                }
            ],
        )
        self.player.known_abilities = ["layered-ward"]
        self.player.health = 50
        self.player.save(update_fields=["known_abilities", "health"])
        mob = self._mob(
            health=self.stats["attack_power"] * 20,
            attack_power=5,
            fights_back=True,
        )
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "layeredward")
                resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        self.assertEqual(encounter.active_effects, [])
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[-1]["data"]["damage_taken"], 0)
        self.assertEqual(attacks[-1]["data"]["damage_absorbed"], 5)

    def test_barrier_damage_type_filter_allows_unmatched_damage_through(self):
        self._ability(
            slug="ability-ward",
            name="Ability Ward",
            verbs=["abilityward"],
            target={"type": "self", "default": "self", "allow_out_of_combat": False},
            components=[
                {
                    "type": "effect",
                    "effect": "ability-ward",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "primitives": [
                        {
                            "type": "damage_absorb",
                            "amount": 10,
                            "calc": "fixed",
                            "damage_types": ["ability"],
                        }
                    ],
                    "apply": "on_resolve",
                }
            ],
        )
        self.player.known_abilities = ["ability-ward"]
        self.player.health = 50
        self.player.save(update_fields=["known_abilities", "health"])
        mob = self._mob(health=self.stats["attack_power"] * 20, attack_power=7, fights_back=True)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "abilityward")
                resolve_combat_encounter(encounter.id)

        self.player.refresh_from_db()
        encounter.refresh_from_db()
        self.assertEqual(self.player.health, 43)
        self.assertEqual(encounter.active_effects[0]["primitives"][0]["remaining"], 10)
        attacks = self._messages_by_type(messages, "notification.combat.attack")
        self.assertEqual(attacks[-1]["data"]["damage_taken"], 7)
        self.assertEqual(attacks[-1]["data"]["damage_absorbed"], 0)

    def test_barrier_can_scale_from_source_ability_power(self):
        self._ability(
            slug="power-ward",
            name="Power Ward",
            verbs=["powerward"],
            target={"type": "self", "default": "self", "allow_out_of_combat": False},
            components=[
                {
                    "type": "effect",
                    "effect": "power-ward",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "primitives": [
                        {
                            "type": "damage_absorb",
                            "amount": 0,
                            "calc": "fixed",
                            "scaling": [
                                {"source": "ability_power", "multiplier": 0.5},
                            ],
                        }
                    ],
                    "apply": "on_resolve",
                }
            ],
        )
        self.player.known_abilities = ["power-ward"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 20, fights_back=False)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            dispatch_text_command(self.player.id, "powerward")
            resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        expected_absorb = math.ceil(self.stats["ability_power"] * 0.5)
        self.assertGreater(expected_absorb, 0)
        self.assertEqual(
            encounter.active_effects[0]["primitives"][0]["remaining"],
            expected_absorb,
        )

    def test_barrier_can_scale_from_multiple_source_stats(self):
        self._ability(
            slug="vital-ward",
            name="Vital Ward",
            verbs=["vitalward"],
            target={"type": "self", "default": "self", "allow_out_of_combat": False},
            components=[
                {
                    "type": "effect",
                    "effect": "vital-ward",
                    "category": "buff",
                    "target": "self",
                    "duration": {"rounds": 3},
                    "primitives": [
                        {
                            "type": "damage_absorb",
                            "amount": 0,
                            "calc": "fixed",
                            "scaling": [
                                {"source": "ability_power", "multiplier": 0.1},
                                {"source": "health_max", "multiplier": 0.3},
                            ],
                        }
                    ],
                    "apply": "on_resolve",
                }
            ],
        )
        self.player.known_abilities = ["vital-ward"]
        self.player.save(update_fields=["known_abilities"])
        mob = self._mob(health=self.stats["attack_power"] * 20, fights_back=False)
        encounter = CombatEncounter.objects.create(
            world=self.spawn_world,
            room=self.room,
            player=self.player,
            mob=mob,
            initiative_order=self._player_first_initiative(mob),
        )

        with patch("spawns.tasks.resolve_combat_encounter.apply_async"):
            dispatch_text_command(self.player.id, "vitalward")
            resolve_combat_encounter(encounter.id)

        encounter.refresh_from_db()
        expected_absorb = math.ceil(
            self.stats["ability_power"] * 0.1
            + self.stats["health_max"] * 0.3
        )
        self.assertGreater(expected_absorb, 0)
        self.assertEqual(
            encounter.active_effects[0]["primitives"][0]["remaining"],
            expected_absorb,
        )

    def test_self_heal_uses_same_ability_schema_outside_combat(self):
        self._ability(
            slug="mend",
            name="Mend",
            verbs=["mend"],
            target={"type": "self", "default": "self", "allow_out_of_combat": True},
            components=[
                {
                    "type": "healing",
                    "profile": "basic_heal",
                    "overrides": {},
                    "text": {"label": "Mend"},
                }
            ],
        )
        self.player.known_abilities = ["mend"]
        self.player.health = 1
        self.player.save(update_fields=["known_abilities", "health"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "mend")

        self.player.refresh_from_db()
        self.assertGreater(self.player.health, 1)
        self.assertFalse(CombatEncounter.objects.filter(player=self.player, status=CombatEncounter.STATUS_ACTIVE).exists())
        self.assertEqual(len(self._messages_by_type(messages, "notification.ability.heal")), 1)
