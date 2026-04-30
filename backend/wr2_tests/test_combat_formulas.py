from config import constants as adv_consts
from core.combat_formulas import normalize_combat_system, resolve_attack
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
            "spell_power": 0,
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
        attacker = self._mob(name="Caster", spell_power=100)
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
