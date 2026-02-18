import random

from backend.config import constants
from backend.core.drops.generation import (
    generate_armor, generate_shield, generate_weapon)
from backend.core.utils import roll_percentage

def generate_equipment(level, quality, eq_type=None, for_archetype=None, main_stat=None, armor_class=None):
    "Routing function for generating equipment stats"

    # Pick an eq type if necessary
    eq_type = eq_type or random.choice(constants.EQUIPMENT_ARMOR)

    # Dispatch
    if eq_type in (constants.EQUIPMENT_TYPE_WEAPON_1H,
                   constants.EQUIPMENT_TYPE_WEAPON_2H):
        return generate_weapon(
            level=level,
            quality=quality,
            eq_type=eq_type,
            for_archetype=for_archetype,
            main_stat=main_stat)
    elif eq_type == constants.EQUIPMENT_TYPE_SHIELD:
        return generate_shield(
            level=level,
            quality=quality,
            for_archetype=for_archetype,
            main_stat=main_stat,
            armor_class=armor_class)
    else:
        return generate_armor(
            level=level,
            quality=quality,
            eq_type=eq_type,
            for_archetype=for_archetype,
            main_stat=main_stat,
            armor_class=armor_class)


def generate_drops(corpse, num_drops=1, chance_normal=100, chance_imbued=20, chance_enchanted=5):
    """
    Generate a random drop in the API and animate its result into the
    game.
    """

    drops = []

    for i in range(0, num_drops):

        # Determine quality
        if roll_percentage(chance_enchanted):
            quality = constants.ITEM_QUALITY_ENCHANTED
        elif roll_percentage(chance_imbued):
            quality = constants.ITEM_QUALITY_IMBUED
        elif roll_percentage(chance_normal):
            quality = constants.ITEM_QUALITY_NORMAL
        else:
            continue

        data = {
            'level': corpse.level,
            'quality': quality,
            'world': corpse.world.id,
        }
        from spawns.serializers import GenerateDropSerializer
        serializer = GenerateDropSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save()

        drops.append(item)

    return drops
