from django.test import SimpleTestCase

from backend.core.drops import generate_equipment
from config import constants as adv_consts


class TestDropGeneration(SimpleTestCase):
    def test_magic_armor_generation_does_not_raise_name_error(self):
        for quality in (
            adv_consts.ITEM_QUALITY_IMBUED,
            adv_consts.ITEM_QUALITY_ENCHANTED,
        ):
            output = generate_equipment(
                level=20,
                quality=quality,
                eq_type=adv_consts.EQUIPMENT_TYPE_BODY,
                armor_class=adv_consts.ARMOR_CLASS_HEAVY,
                main_stat=adv_consts.ATTR_CON,
            )
            self.assertEqual(output["equipment_type"], adv_consts.EQUIPMENT_TYPE_BODY)
            self.assertEqual(output["armor_class"], adv_consts.ARMOR_CLASS_HEAVY)
            self.assertTrue(output.get("name"))

    def test_magic_weapon_generation_uses_weapon_classification_map(self):
        output = generate_equipment(
            level=20,
            quality=adv_consts.ITEM_QUALITY_IMBUED,
            eq_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
            main_stat=adv_consts.ATTR_STR,
        )
        self.assertEqual(output["equipment_type"], adv_consts.EQUIPMENT_TYPE_WEAPON_1H)
        self.assertTrue(output.get("name"))
