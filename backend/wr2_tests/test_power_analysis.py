from rest_framework.reverse import reverse

from builders.balance.power_analysis import (
    analyze_item_definition_power,
    analyze_mob_definition_power,
)
from builders.models import ItemDefinition, MobDefinition
from config import constants as adv_consts
from tests.base import WorldTestCase
from wr2_tests.utils import apply_basic_stat_system


class TestBuilderPowerAnalysis(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        apply_basic_stat_system(self.world)
        self.world.config.equipment_system = {
            "armor_classes": [
                {
                    "key": "light",
                    "label": "Light Armor",
                    "armor_multiplier": 1.0,
                },
                {
                    "key": "heavy",
                    "label": "Heavy Armor",
                    "armor_multiplier": 1.5,
                },
            ],
            "default_armor_class": "light",
            "armor_suggestions": {
                "full_set_scale": 0.4,
                "slot_weights": {
                    "head": 0.15,
                    "body": 0.30,
                    "arms": 0.10,
                    "hands": 0.10,
                    "waist": 0.10,
                    "legs": 0.15,
                    "feet": 0.10,
                    "shield": 0.35,
                },
            },
        }
        self.world.config.save(update_fields=["equipment_system"])

    def test_item_power_analysis_reports_slot_reference_and_drivers(self):
        definition = ItemDefinition.objects.create(
            world=self.world,
            slug="round-shield",
            name="a round shield",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "level": 10,
                "equipment_type": adv_consts.EQUIPMENT_TYPE_SHIELD,
                "armor_class": "heavy",
                "armor": 30,
                "health_max": 5,
            },
            attributes={"grit": 2},
        )

        analysis = analyze_item_definition_power(self.world, definition)

        self.assertEqual(analysis["kind"], "itemdefinition")
        self.assertEqual(analysis["summary"]["equipment_type"], adv_consts.EQUIPMENT_TYPE_SHIELD)
        self.assertEqual(analysis["metrics"]["slot_weight"], 0.35)
        self.assertGreater(analysis["metrics"]["expected_slot_armor"], 0)
        self.assertGreater(analysis["summary"]["budget_score"], 0)
        self.assertTrue(
            any(driver["stat"] == "armor" for driver in analysis["drivers"])
        )

    def test_mob_power_analysis_reports_combat_metrics(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="guard",
            name="a guard",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={
                "level": 8,
                "health_max": 80,
                "attack_power": 20,
                "weapon_damage": 24,
                "armor": 12,
                "dodge": 4,
            },
            attributes={"brawn": 3},
        )

        analysis = analyze_mob_definition_power(self.world, definition)

        self.assertEqual(analysis["kind"], "mobdefinition")
        self.assertEqual(analysis["summary"]["level"], 8)
        self.assertAlmostEqual(analysis["metrics"]["basic_attack_base"], 24 + round(23 / 16, 2))
        self.assertGreater(analysis["metrics"]["expected_basic_attack"], 0)
        self.assertGreater(analysis["metrics"]["physical_effective_health"], 80)
        self.assertTrue(
            any(driver["stat"] == "weapon_damage" for driver in analysis["drivers"])
        )

    def test_power_endpoints_return_analysis_payloads(self):
        item_definition = ItemDefinition.objects.create(
            world=self.world,
            slug="bronze-sword",
            name="a bronze sword",
            item_type=adv_consts.ITEM_TYPE_EQUIPPABLE,
            base_properties={
                "level": 6,
                "equipment_type": adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                "weapon_damage": 8,
            },
        )
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            slug="bandit",
            name="a bandit",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            base_properties={
                "level": 6,
                "health_max": 45,
                "attack_power": 8,
                "weapon_damage": 12,
            },
        )

        item_resp = self.client.get(
            reverse(
                "builder-item-definition-power",
                args=[self.world.pk, item_definition.pk],
            )
        )
        mob_resp = self.client.get(
            reverse(
                "builder-mob-definition-power",
                args=[self.world.pk, mob_definition.pk],
            )
        )

        self.assertEqual(item_resp.status_code, 200, item_resp.data)
        self.assertEqual(item_resp.data["kind"], "itemdefinition")
        self.assertEqual(mob_resp.status_code, 200, mob_resp.data)
        self.assertEqual(mob_resp.data["kind"], "mobdefinition")
