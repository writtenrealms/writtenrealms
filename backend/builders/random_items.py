import random
from core import utils as adv_utils

from config import constants as adv_consts
from core.utils.items import price_item

from config import constants as api_consts
from spawns.models import Item

from backend.core.drops import generate_equipment
from backend.core.drops import generation as drops_generation


def generate_archetype_characteristics(archetype):

    if adv_utils.roll_percentage(25):
        main_stat = adv_consts.ATTR_CON
    elif archetype == adv_consts.ARCHETYPE_WARRIOR:
        main_stat = adv_consts.ATTR_STR
    elif archetype == adv_consts.ARCHETYPE_ASSASSIN:
        main_stat = adv_consts.ATTR_DEX
    else:
        main_stat = adv_consts.ATTR_INT

    if archetype == adv_consts.ARCHETYPE_WARRIOR:
        armor_class = adv_consts.ARMOR_CLASS_HEAVY
    else:
        armor_class = adv_consts.ARMOR_CLASS_LIGHT

    return {
        'main_stat': main_stat,
        'armor_class': armor_class
    }


def generate_item(char, chance_imbued, chance_enchanted, specification,
    level=None, generate_normal=True, for_archetype=False):
    """
    If `generate_normal` is set to False, None will returned rather than
    a normal item if neither the enchanted not the imbued roll land.

    If `for_archetype` is True, the generated item will
    roll a desirable primary attribute based on the character's archetype, and
    generate if rolling armor will generate the desired armor class.
    """

    if adv_utils.roll_percentage(chance_enchanted):
        quality = adv_consts.ITEM_QUALITY_ENCHANTED
    elif adv_utils.roll_percentage(chance_imbued):
        quality = adv_consts.ITEM_QUALITY_IMBUED
    else:
        quality = adv_consts.ITEM_QUALITY_NORMAL
        if not generate_normal:
            return None

    if level is None:
        level = char.level

    main_stat = None
    armor_class = None
    if for_archetype:
        if for_archetype == True:
            for_archetype = char.archetype
        archetype_characteristics = generate_archetype_characteristics(
            for_archetype)
        main_stat = archetype_characteristics['main_stat']
        armor_class = archetype_characteristics['armor_class']

    attrs = {}
    if specification == api_consts.ITEM_SPECIFICATION_WEAPON:
        if adv_utils.roll_percentage(25):
            eq_type = adv_consts.EQUIPMENT_TYPE_WEAPON_2H
        else:
            eq_type = adv_consts.EQUIPMENT_TYPE_WEAPON_1H
        attrs = drops_generation.generate_weapon(
            level=level,
            quality=quality,
            eq_type=eq_type,
            main_stat=main_stat,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_WEAPON_1H:
        attrs = drops_generation.generate_weapon(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
            main_stat=main_stat,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_WEAPON_2H:
        attrs = drops_generation.generate_weapon(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_WEAPON_2H,
            main_stat=main_stat,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_SHIELD:
        attrs = drops_generation.generate_shield(
            level=level,
            quality=quality,
            main_stat=main_stat,
            armor_class=armor_class,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_WEAPON_OR_SHIELD:
        if adv_utils.roll_percentage(25):
            attrs = drops_generation.generate_shield(
                level=level,
                quality=quality,
                main_stat=main_stat,
                armor_class=armor_class,
            for_archetype=for_archetype)
        elif adv_utils.roll_percentage(25):
            attrs = drops_generation.generate_weapon(
                level=level,
                quality=quality,
                eq_type=adv_consts.EQUIPMENT_TYPE_WEAPON_2H,
                main_stat=main_stat,
            for_archetype=for_archetype)
        else:
            attrs = drops_generation.generate_weapon(
                level=level,
                quality=quality,
                eq_type=adv_consts.EQUIPMENT_TYPE_WEAPON_1H,
                main_stat=main_stat,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_ARMOR_HEAVY:
        eq_type = random.choice(adv_consts.EQUIPMENT_ARMOR)
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=eq_type,
            armor_class=adv_consts.ARMOR_CLASS_HEAVY,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_ARMOR_LIGHT:
        eq_type = random.choice(adv_consts.EQUIPMENT_ARMOR)
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=eq_type,
            armor_class=adv_consts.ARMOR_CLASS_LIGHT,
            for_archetype=for_archetype)
    # Light armor specific slots
    elif specification == api_consts.ITEM_SPECIFICATION_LIGHT_HEAD:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_HEAD,
            armor_class=adv_consts.ARMOR_CLASS_LIGHT,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_LIGHT_BODY:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_BODY,
            armor_class=adv_consts.ARMOR_CLASS_LIGHT,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_LIGHT_ARMS:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_ARMS,
            armor_class=adv_consts.ARMOR_CLASS_LIGHT,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_LIGHT_HANDS:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_HANDS,
            armor_class=adv_consts.ARMOR_CLASS_LIGHT,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_LIGHT_WAIST:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_WAIST,
            armor_class=adv_consts.ARMOR_CLASS_LIGHT,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_LIGHT_LEGS:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_LEGS,
            armor_class=adv_consts.ARMOR_CLASS_LIGHT,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_LIGHT_FEET:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_FEET,
            armor_class=adv_consts.ARMOR_CLASS_LIGHT,
            for_archetype=for_archetype)
    # Heavy armor specific slots
    elif specification == api_consts.ITEM_SPECIFICATION_HEAVY_HEAD:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_HEAD,
            armor_class=adv_consts.ARMOR_CLASS_HEAVY,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_HEAVY_BODY:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_BODY,
            armor_class=adv_consts.ARMOR_CLASS_HEAVY,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_HEAVY_ARMS:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_ARMS,
            armor_class=adv_consts.ARMOR_CLASS_HEAVY,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_HEAVY_HANDS:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_HANDS,
            armor_class=adv_consts.ARMOR_CLASS_HEAVY,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_HEAVY_WAIST:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_WAIST,
            armor_class=adv_consts.ARMOR_CLASS_HEAVY,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_HEAVY_LEGS:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_LEGS,
            armor_class=adv_consts.ARMOR_CLASS_HEAVY,
            for_archetype=for_archetype)
    elif specification == api_consts.ITEM_SPECIFICATION_HEAVY_FEET:
        attrs = drops_generation.generate_armor(
            level=level,
            quality=quality,
            eq_type=adv_consts.EQUIPMENT_TYPE_FEET,
            armor_class=adv_consts.ARMOR_CLASS_HEAVY,
            for_archetype=for_archetype)
    else:
        attrs = generate_equipment(
            level=level,
            quality=quality,
            for_archetype=for_archetype,
            main_stat=main_stat,
            armor_class=armor_class)

    # Price the random item
    attrs['cost'] = price_item(
        level=level,
        quality=quality,
        eq_type=attrs.get('equipment_type'))

    return Item.objects.create(
        world=char.world,
        quality=quality,
        level=level,
        type=adv_consts.ITEM_TYPE_EQUIPPABLE,
        container=char,
        **attrs)
