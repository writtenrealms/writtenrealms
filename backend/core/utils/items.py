import math

from config import constants
from config import game_settings as config


def get_slot_constant(eq_type):
    if eq_type == constants.EQUIPMENT_TYPE_WEAPON_2H:
        return 2.5
    elif eq_type in (
        constants.EQUIPMENT_TYPE_WEAPON_1H,
        constants.EQUIPMENT_TYPE_SHIELD,
        constants.EQUIPMENT_TYPE_HEAD,
        constants.EQUIPMENT_TYPE_BODY,
        constants.EQUIPMENT_TYPE_LEGS):
        return 1.25
    elif eq_type:
        return 1
    return 0


def type_to_slot(eq_type, has_weapon=False, has_offhand=False, archetype=None):
    "For a given equipment type, return the slot it should be equipped in"
    if eq_type == constants.EQUIPMENT_TYPE_WEAPON_2H:
        return constants.EQUIPMENT_SLOT_WEAPON
    elif eq_type == constants.EQUIPMENT_TYPE_WEAPON_1H:
        if not has_weapon:
            return constants.EQUIPMENT_SLOT_WEAPON
        elif not has_offhand and archetype == constants.ARCHETYPE_ASSASSIN:
            return constants.EQUIPMENT_SLOT_OFFHAND
    elif eq_type == constants.EQUIPMENT_TYPE_SHIELD:
        return constants.EQUIPMENT_SLOT_OFFHAND
    elif eq_type in constants.EQUIPMENT_SLOTS:
        return eq_type
    return None


def get_main_primary_stat(stats):
    "Returns the input attribute with the greatest value."
    max = 0
    max_stat = None
    for stat, value in (stats.get("input_attributes") or {}).items():
        if max < value:
            max = value
            max_stat = stat
    # The old procedural drop generator still names items before generated
    # values are folded into world-declared input_attributes.
    for stat in constants.PRIMARY_ATTRIBUTES:
        if max < stats.get(stat, 0):
            max = stats[stat]
            max_stat = stat
    return max_stat


def get_item_budget(level, eq_type, enchanted=False):
    budget = math.ceil(get_slot_constant(eq_type) * config.ILF(level) * 20)
    if enchanted:
        budget *= 1.2
    return budget


def calculate_power(item, archetype):
    """
    Given a certain archetype using an item, return an estimate for
    how strong that item is compared to others.

    World-authored input attributes are intentionally generic here. Canonical
    combat stats still use the existing budget weights.
    """
    from config import constants as adv_consts

    total_value = 0
    for stat_value in (item.input_attributes or {}).values():
        if stat_value:
            total_value += stat_value * 10
    for stat, weight in adv_consts.ATTR_BUDGET.items():
        stat_value = getattr(item, stat, 0)
        if stat_value:
            stat_value = stat_value * weight
            total_value += stat_value
    return total_value


def price_item(level, quality, eq_type=None, upgrade_count=0):
    # Base cost
    ilf_cost = config.ILF(level)

    # Imbued / Enchanted
    if quality == constants.ITEM_QUALITY_IMBUED:
        ilf_cost *= 3
    elif quality == constants.ITEM_QUALITY_ENCHANTED:
        ilf_cost *= 5

    # Factor in slot constant
    if eq_type:
        ilf_cost *= constants.get_slot_constant(eq_type)

    # Factor in upgrades
    ilf_cost = ilf_cost + ilf_cost * upgrade_count * 0.25

    return round(ilf_cost)
