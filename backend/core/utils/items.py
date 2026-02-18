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
    "Returns the name of the primary stat that has the greatest value"
    max = 0
    max_stat = None
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
    Given a certain archtype using an item, return an estimate for
    how strong that item is compared to others.

    This uses two weights:
    1) the ATTR_BUDGET mapping in adv_consts, where for example
    1 hp regen is worth 4 times more than 1 primary stat
    2) A per-class map of how valuable each attribute is
    """
    from config import constants as adv_consts


    CLASS_WEIGHTS = {
        'warrior': {
            adv_consts.ATTR_REGEN_HEALTH: 10,
            adv_consts.ATTR_CON: 10,
            adv_consts.ATTR_STR: 10,
            adv_consts.ATTR_MAX_HEALTH: 8,
            adv_consts.ATTR_AP: 7,
            adv_consts.ATTR_RESILIENCE: 6,
            adv_consts.ATTR_CRIT: 6,
            adv_consts.ATTR_DEX: 4,
            adv_consts.ATTR_DODGE: 4,
            adv_consts.ATTR_REGEN_MANA: 0,
            adv_consts.ATTR_INT: 0,
            adv_consts.ATTR_MAX_MANA: 0,
            adv_consts.ATTR_SP: 0,
        },
        'mage': {
            adv_consts.ATTR_CON: 10,
            adv_consts.ATTR_INT: 10,
            adv_consts.ATTR_MAX_HEALTH: 8,
            adv_consts.ATTR_SP: 8,
            adv_consts.ATTR_MAX_MANA: 7,
            adv_consts.ATTR_REGEN_HEALTH: 6,
            adv_consts.ATTR_REGEN_MANA: 6,
            adv_consts.ATTR_STR: 4,
            adv_consts.ATTR_DEX: 4,
            adv_consts.ATTR_CRIT: 3,
            adv_consts.ATTR_RESILIENCE: 3,
            adv_consts.ATTR_DODGE: 3,
            adv_consts.ATTR_AP: 1,
        },
        'cleric': {
            adv_consts.ATTR_INT: 10,
            adv_consts.ATTR_CON: 10,
            adv_consts.ATTR_REGEN_MANA: 8,
            adv_consts.ATTR_MAX_HEALTH: 8,
            adv_consts.ATTR_SP: 8,
            adv_consts.ATTR_MAX_MANA: 7,
            adv_consts.ATTR_REGEN_HEALTH: 6,
            adv_consts.ATTR_STR: 4,
            adv_consts.ATTR_DEX: 4,
            adv_consts.ATTR_CRIT: 3,
            adv_consts.ATTR_RESILIENCE: 3,
            adv_consts.ATTR_DODGE: 3,
            adv_consts.ATTR_AP: 1,
        },
        'assassin': {
            adv_consts.ATTR_REGEN_HEALTH: 10,
            adv_consts.ATTR_CON: 10,
            adv_consts.ATTR_DEX: 10,
            adv_consts.ATTR_AP: 8,
            adv_consts.ATTR_MAX_HEALTH: 7,
            adv_consts.ATTR_STR: 6,
            adv_consts.ATTR_CRIT: 5,
            adv_consts.ATTR_DODGE: 5,
            adv_consts.ATTR_RESILIENCE: 4,
            adv_consts.ATTR_REGEN_MANA: 0,
            adv_consts.ATTR_MAX_MANA: 0,
            adv_consts.ATTR_SP: 0,
            adv_consts.ATTR_INT: 0,
        },
    }

    boosted_stats = {}
    total_value = 0
    for stat, weight in adv_consts.ATTR_BUDGET.items():
        stat_value = getattr(item, stat)
        if stat_value:
            stat_value = stat_value * weight
            stat_value *= CLASS_WEIGHTS[archetype][stat]
            boosted_stats[stat] = stat_value
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
