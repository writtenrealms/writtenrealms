from copy import deepcopy
import math
from unittest.mock import patch

from builders.models import AbilityDefinition, ItemTemplate, MobDefinition
from config import constants as adv_consts
from core.combat_formulas import CombatAttackResult, normalize_combat_system, resolve_attack
from core.computations import compute_stats
from core.scoped_state import STATE_SCOPE_CHARACTER, get_state_value
from django.utils import timezone
from spawns.actions.movement_costs import movement_cost
from spawns.models import CombatEncounter, Item, Mob, Player
from spawns.tasks import resolve_combat_encounter
from tests.base import WorldTestCase
from wr2_tests.utils import (
    BASIC_TEST_STAT_SYSTEM,
    apply_basic_stat_system,
    capture_game_messages,
    dispatch_text_command,
)


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
        consumes_primary_action=True,
    ):
        return AbilityDefinition.objects.create(
            world=self.world,
            slug=slug,
            name=name,
            command_verbs=verbs,
            action_type="primary",
            consumes_primary_action=consumes_primary_action,
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

    def test_definition_backed_mob_uses_combat_ability_loadout(self):
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

    def test_percent_base_cost_uses_energy_base_before_equipment_modifiers(self):
        from spawns.actions.abilities import ability_cost_amount

        stat_system = deepcopy(BASIC_TEST_STAT_SYSTEM)
        stat_system["formulas"]["base_resources"]["energy"] = {"flat": 100}
        self.world.config.stat_system = stat_system
        self.world.config.save(update_fields=["stat_system"])

        template = ItemTemplate.objects.create(
            world=self.world,
            name="Focus Ring",
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=adv_consts.EQUIPMENT_TYPE_ACCESSORY,
        )
        ring = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=template,
            name=template.name,
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

        template = ItemTemplate.objects.create(
            world=self.world,
            name="Vital Ring",
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=adv_consts.EQUIPMENT_TYPE_ACCESSORY,
        )
        ring = Item.objects.create(
            world=self.spawn_world,
            container=self.player,
            template=template,
            name=template.name,
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
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        MobDefinition.objects.create(
            world=self.world,
            slug="arms-trainer",
            name="an arms trainer",
            keywords="trainer arms",
            base_properties={"health_max": 10},
            trainer={
                "abilities": ["power-strike"],
                "availability": "present",
            },
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
        self.assertEqual(lists[0]["text"], "You can learn here: Field Mend [ learn mend ].")
        self.assertEqual(
            lists[0]["data"]["abilities"],
            [
                {
                    "slug": "field-mend",
                    "name": "Field Mend",
                    "learn_command": "learn mend",
                    "trainer": None,
                }
            ],
        )

    def test_learn_without_selector_lists_trainable_abilities_at_trainer(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        self._ability(
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
        trainer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="arms-trainer",
            name="an arms trainer",
            keywords="trainer arms",
            base_properties={"health_max": 10},
            trainer={
                "abilities": ["power-strike", "locked-strike"],
                "availability": "present",
            },
        )
        trainer = trainer_definition.spawn(self.room, self.spawn_world)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "learn")

        lists = self._messages_by_type(messages, "cmd.ability.learn.list")
        self.assertEqual(len(lists), 1)
        self.assertEqual(
            lists[0]["text"],
            "You can learn here: Power Strike [ learn strike ], Field Mend [ learn mend ].",
        )
        self.assertEqual(
            lists[0]["data"]["abilities"],
            [
                {
                    "slug": "power-strike",
                    "name": "Power Strike",
                    "learn_command": "learn strike",
                    "trainer": {"id": trainer.id, "name": "an arms trainer"},
                },
                {
                    "slug": "field-mend",
                    "name": "Field Mend",
                    "learn_command": "learn mend",
                    "trainer": None,
                },
            ],
        )
        self.assertEqual(
            lists[0]["data"]["trainers"],
            [{"id": trainer.id, "name": "an arms trainer"}],
        )

    def test_learning_trainer_gated_ability_requires_present_trainer(self):
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        trainer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="arms-trainer",
            name="an arms trainer",
            keywords="trainer arms",
            base_properties={"health_max": 10},
            trainer={
                "abilities": ["power-strike"],
                "availability": "present",
            },
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
        self._ability(
            slug="power-strike",
            name="Power Strike",
            verbs=["strike"],
            components=[{"type": "damage", "profile": "basic_physical"}],
        )
        trainer_definition = MobDefinition.objects.create(
            world=self.world,
            slug="arms-trainer",
            name="an arms trainer",
            keywords="trainer arms",
            base_properties={"health_max": 10},
            trainer={
                "abilities": ["power-strike"],
                "availability": "present",
            },
        )
        self.player.known_abilities = ["power-strike"]
        self.player.ability_hotkeys = {"1": "power-strike"}
        self.player.save(update_fields=["known_abilities", "ability_hotkeys"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "unlearn power strike")

        self.player.refresh_from_db()
        self.assertEqual(self.player.known_abilities, ["power-strike"])
        self.assertEqual(self.player.ability_hotkeys, {"1": "power-strike"})
        errors = self._messages_by_type(messages, "cmd.ability.unlearn.error")
        self.assertEqual(errors[0]["data"]["code"], "ability_trainer_required")

        trainer_definition.spawn(self.room, self.spawn_world)
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "unlearn power strike")

        self.player.refresh_from_db()
        self.assertEqual(self.player.known_abilities, [])
        self.assertEqual(self.player.ability_hotkeys, {})
        success = self._messages_by_type(messages, "cmd.ability.unlearn.success")[0]
        self.assertEqual(success["data"]["trainer"]["name"], "an arms trainer")

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
                "consumes_primary_action"
            ]
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

    def test_charge_moves_to_adjacent_room_and_attacks_with_opener_priority(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        self._charge_ability()
        self.player.known_abilities = ["charge"]
        self.player.save(update_fields=["known_abilities"])
        dest_room = self.room.create_at(adv_consts.DIRECTION_EAST)
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

        self.assertEqual(self.player.ability_cooldowns, {"charge": 10})
        schedule_mock.assert_called_once()
        self.assertEqual(
            schedule_mock.call_args.kwargs["kwargs"]["encounter_id"],
            encounter.id,
        )
        self.assertEqual(schedule_mock.call_args.kwargs["countdown"], 1.5)

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
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_text_command(self.player.id, "strike rat")
            with capture_game_messages() as messages:
                dispatch_text_command(self.player.id, "jab rat")

        encounter = CombatEncounter.objects.get(player=self.player, mob=mob, status=CombatEncounter.STATUS_ACTIVE)
        self.assertEqual(encounter.pending_player_ability["ability"], "quick-jab")
        self.assertEqual(self._messages_by_type(messages, "cmd.ability.success")[0]["text"], "You switch to Quick Jab.")
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
            active_effects=[
                {
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
                }
            ],
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
            consumes_primary_action=False,
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

        self.player.active_effects[0]["remaining_rounds"] = 2
        ally.active_effects[0]["remaining_rounds"] = 2
        self.player.save(update_fields=["active_effects"])
        ally.save(update_fields=["active_effects"])

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
        self.player.active_effects = [
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
        ]
        self.player.save(update_fields=["active_effects"])
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
