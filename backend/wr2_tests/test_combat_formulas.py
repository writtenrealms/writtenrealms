from config import constants as adv_consts
from config import game_settings as adv_config
from core.combat_formulas import (
    CombatFormulaValidationError,
    _level_scale,
    _rating_percent,
    get_world_combat_system,
    normalize_combat_system,
    resolve_attack,
)
from spawns.models import Item, Mob
from tests.base import WorldTestCase


class TestCombatFormulaResolution(WorldTestCase):
    def _configure_combat(self, combat_patch):
        self.world.config.combat_system = normalize_combat_system(combat_patch)
        self.world.config.save(update_fields=["combat_system"])

    def _mob(self, name="Target", **kwargs):
        defaults = {
            "world": self.spawn_world,
            "room": self.room,
            "name": name,
            "keywords": name.lower(),
            "health": 100,
            "health_max": 100,
            "attack_power": 0,
            "ability_power": 0,
            "weapon_damage": 0,
            "armor": 0,
            "resilience": 0,
            "dodge": 0,
            "crit": 0,
        }
        defaults.update(kwargs)
        return Mob.objects.create(**defaults)

    def _equip_weapon(self, *, weapon_damage):
        weapon = Item.objects.create(
            world=self.spawn_world,
            name="a test sword",
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
            weapon_damage=weapon_damage,
        )
        self.player.equipment.equip(weapon, adv_consts.EQUIPMENT_SLOT_WEAPON)
        return weapon

    def _equip_mob_weapon(self, mob, *, weapon_damage, attack_power=0):
        weapon = Item.objects.create(
            world=self.spawn_world,
            name="a mob test sword",
            type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            equipment_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
            weapon_damage=weapon_damage,
            attack_power=attack_power,
        )
        mob.equipment.equip(weapon, adv_consts.EQUIPMENT_SLOT_WEAPON)
        return weapon

    def test_default_level_scale_is_open_ended_exponential(self):
        combat_system = normalize_combat_system({})

        self.assertEqual(combat_system["level_scale"]["type"], "exponential")
        self.assertAlmostEqual(
            _level_scale(20, combat_system),
            5.5 * (1.1 ** 20),
        )
        self.assertGreater(
            _level_scale(60, combat_system),
            _level_scale(20, combat_system),
        )

    def test_default_rating_types_keep_rating_curve_behavior(self):
        combat_system = normalize_combat_system({})

        self.assertEqual(
            combat_system["ratings"]["dodge"]["type"],
            "mitigation_curve",
        )
        self.assertEqual(
            combat_system["ratings"]["crit"]["type"],
            "linear_rating",
        )
        self.assertEqual(
            combat_system["ratings"]["armor"]["type"],
            "mitigation_curve",
        )
        self.assertEqual(
            combat_system["ratings"]["resilience"]["type"],
            "mitigation_curve",
        )

    def test_empty_world_combat_config_uses_rating_curve_defaults(self):
        self.assertEqual(self.world.config.combat_system, {})

        combat_system = get_world_combat_system(self.spawn_world)

        self.assertEqual(
            combat_system["ratings"]["dodge"]["type"],
            "mitigation_curve",
        )
        self.assertEqual(
            combat_system["ratings"]["crit"]["type"],
            "linear_rating",
        )
        self.assertEqual(
            combat_system["ratings"]["armor"]["type"],
            "mitigation_curve",
        )
        self.assertEqual(
            combat_system["ratings"]["resilience"]["type"],
            "mitigation_curve",
        )

    def test_percentage_point_rating_ignores_opponent_level(self):
        combat_system = normalize_combat_system({
            "ratings": {
                "dodge": {
                    "stat": "dodge",
                    "type": "percentage_points",
                    "base": 0,
                    "cap": 0.75,
                },
            },
        })
        rating_config = combat_system["ratings"]["dodge"]

        self.assertNotIn("constant", rating_config)
        self.assertAlmostEqual(
            _rating_percent(
                rating_config=rating_config,
                rating=1,
                opponent_level=1,
                combat_system=combat_system,
            ),
            0.01,
        )
        self.assertAlmostEqual(
            _rating_percent(
                rating_config=rating_config,
                rating=1,
                opponent_level=20,
                combat_system=combat_system,
            ),
            0.01,
        )
        self.assertAlmostEqual(
            _rating_percent(
                rating_config=rating_config,
                rating=1000,
                opponent_level=20,
                combat_system=combat_system,
            ),
            0.75,
        )

    def test_linear_level_scale_is_configurable(self):
        combat_system = normalize_combat_system({
            "level_scale": {
                "type": "linear",
                "base": 10,
                "per_level": 2,
            },
        })

        self.assertEqual(
            combat_system["level_scale"],
            {
                "type": "linear",
                "base": 10.0,
                "per_level": 2.0,
            },
        )
        self.assertEqual(_level_scale(5, combat_system), 20)

    def test_flat_level_scale_is_configurable(self):
        combat_system = normalize_combat_system({
            "level_scale": {
                "type": "flat",
                "value": 7,
            },
        })

        self.assertEqual(
            combat_system["level_scale"],
            {
                "type": "flat",
                "value": 7.0,
            },
        )
        self.assertEqual(_level_scale(60, combat_system), 7)

    def test_ilf_level_scale_keeps_wr1_taper(self):
        combat_system = normalize_combat_system({
            "level_scale": {
                "type": "ilf",
            },
        })

        self.assertEqual(combat_system["level_scale"], {"type": "ilf"})
        self.assertAlmostEqual(_level_scale(16, combat_system), adv_config.ILF(16))
        self.assertAlmostEqual(_level_scale(20, combat_system), adv_config.ILF(20))
        self.assertAlmostEqual(_level_scale(60, combat_system), adv_config.ILF(20))

    def test_unknown_level_scale_type_is_rejected(self):
        with self.assertRaises(CombatFormulaValidationError):
            normalize_combat_system({
                "level_scale": {
                    "type": "sqrt",
                },
            })

    def test_weapon_damage_is_first_class_damage_input(self):
        self._configure_combat({
            "variance": {
                "enabled": False,
                "percent": 0,
            },
            "profiles": {
                "basic_physical": {
                    "power_scale": 0,
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
        self._equip_weapon(weapon_damage=30)
        target = self._mob()

        result = resolve_attack(
            actor=self.player,
            target=target,
            world=self.spawn_world,
        )

        self.assertEqual(result.damage_base, 30)
        self.assertEqual(result.damage_dealt, 30)
        self.assertEqual(result.damage_taken, 30)

    def test_mob_internal_weapon_damage_drives_basic_attack(self):
        self._configure_combat({
            "variance": {
                "enabled": False,
                "percent": 0,
            },
            "profiles": {
                "basic_physical": {
                    "power_scale": 0,
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
        attacker = self._mob(name="Attacker", weapon_damage=40)
        self._equip_mob_weapon(attacker, weapon_damage=999, attack_power=999)
        target = self._mob()

        result = resolve_attack(
            actor=attacker,
            target=target,
            world=self.spawn_world,
        )

        self.assertEqual(result.damage_base, 40)
        self.assertEqual(result.damage_dealt, 40)

    def test_disarmed_mob_uses_unarmed_damage_multiplier(self):
        self._configure_combat({
            "variance": {
                "enabled": False,
                "percent": 0,
            },
            "profiles": {
                "basic_physical": {
                    "power_scale": 0,
                    "mob_unarmed_damage_multiplier": 0.2,
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
        attacker = self._mob(name="Attacker", weapon_damage=50)
        target = self._mob()

        armed_result = resolve_attack(
            actor=attacker,
            target=target,
            world=self.spawn_world,
        )
        disarmed_result = resolve_attack(
            actor=attacker,
            target=target,
            world=self.spawn_world,
            actor_disarmed=True,
        )

        self.assertEqual(armed_result.damage_base, 50)
        self.assertEqual(disarmed_result.damage_base, 10)

    def test_mob_without_weapon_damage_keeps_level_based_fallback(self):
        self._configure_combat({
            "level_scale": {
                "type": "flat",
                "value": 20,
            },
            "variance": {
                "enabled": False,
                "percent": 0,
            },
            "profiles": {
                "basic_physical": {
                    "power_scale": 0,
                    "mob_unarmed_level_scale": 0.5,
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
        attacker = self._mob(name="Attacker")
        target = self._mob()

        result = resolve_attack(
            actor=attacker,
            target=target,
            world=self.spawn_world,
        )

        self.assertEqual(result.damage_base, 10)

    def test_physical_damage_uses_armor_not_resilience_by_default(self):
        self._configure_combat({
            "variance": {
                "enabled": False,
                "percent": 0,
            },
            "profiles": {
                "basic_physical": {
                    "power_scale": 0,
                    "can_dodge": False,
                    "can_crit": False,
                    "minimum": 0,
                },
            },
        })
        self._equip_weapon(weapon_damage=100)
        resilient_target = self._mob(resilience=1000)
        armored_target = self._mob(armor=1000, resilience=1000)

        resilient_result = resolve_attack(
            actor=self.player,
            target=resilient_target,
            world=self.spawn_world,
        )
        armored_result = resolve_attack(
            actor=self.player,
            target=armored_target,
            world=self.spawn_world,
        )

        self.assertEqual(resilient_result.damage_taken, 100)
        self.assertEqual(resilient_result.resilience_mitigation, 0)
        self.assertLess(armored_result.damage_taken, 100)
        self.assertGreater(armored_result.armor_mitigation, 0)
        self.assertEqual(armored_result.resilience_mitigation, 0)

    def test_ability_damage_uses_resilience_not_armor_by_default(self):
        self._configure_combat({
            "variance": {
                "enabled": False,
                "percent": 0,
            },
            "profiles": {
                "basic_ability": {
                    "power_scale": 1,
                    "can_crit": False,
                    "minimum": 0,
                },
            },
        })
        attacker = self._mob(name="Caster", ability_power=100)
        armored_target = self._mob(name="Armored", armor=1000)
        resilient_target = self._mob(name="Resilient", resilience=1000)

        armored_result = resolve_attack(
            actor=attacker,
            target=armored_target,
            world=self.spawn_world,
            profile_key="basic_ability",
        )
        resilient_result = resolve_attack(
            actor=attacker,
            target=resilient_target,
            world=self.spawn_world,
            profile_key="basic_ability",
        )

        self.assertEqual(armored_result.damage_taken, 100)
        self.assertEqual(armored_result.armor_mitigation, 0)
        self.assertLess(resilient_result.damage_taken, 100)
        self.assertGreater(resilient_result.resilience_mitigation, 0)
