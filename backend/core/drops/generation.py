import math
import random

from backend.config import constants
from backend.config import game_settings as config
from backend.core.drops import (
    utils as drop_utils,
    naming)
from backend.core.utils import roll_percentage
from backend.core.utils import items as item_utils, roll_variance

def generate_boosted_stats(level, eq_type, quality, main_stat=None, exclude_stats=None, for_archetype=None):

    boosted_stats = []
    budget = item_utils.get_item_budget(level, eq_type)

    # If we're passing a main stat in, the assumption is that we are rolling
    # an item for a specific archetype. When we do that, we weed out stats
    # that are undesirable for the archetype.
    if for_archetype and not exclude_stats:
        if for_archetype in (
            constants.ARCHETYPE_WARRIOR,
            constants.ARCHETYPE_ASSASSIN):
            exclude_stats = [
                constants.ATTR_INT,
                constants.ATTR_MAX_MANA,
                constants.ATTR_REGEN_MANA,
                constants.ATTR_SP,
            ]
        else:
            exclude_stats = [
                constants.ATTR_DODGE,
                constants.ATTR_AP,
                constants.ATTR_DEX,
            ]

    if quality == constants.ITEM_QUALITY_ENCHANTED:
        # Enchanted items get a budget boost
        budget = math.ceil(budget * 1.2)

        # First pass 40-60% to the primary stat
        # and then keep the reminaing % unspent
        boosted_stats.extend(
            drop_utils.spend_budget(
                budget=budget,
                min=0.4, max=0.6,
                compute_secondary=False,
                primary_attr=main_stat,
                exclude_stats=exclude_stats))

        # Second pass, split the remainder 50-60% to the primary stat
        boosted_stats.extend(
            drop_utils.spend_budget(
                budget=budget - boosted_stats[0]['budget_used'],
                min=0.5, max=0.6,
                exclude_stats=exclude_stats))
    else:
        boosted_stats.extend(
            drop_utils.spend_budget(
                budget=budget,
                min=0.5, max=0.8,
                primary_attr=main_stat,
                exclude_stats=exclude_stats))

    stats = {}
    for stat in boosted_stats:

        value = roll_variance(stat['points'], config.RANDOM_ROLL_VARIANCE)

        if stat['name'] in stats:
            stats[stat['name']] += value
        else:
            stats[stat['name']] = value

    return stats

def generate_armor(level, quality, eq_type, armor_class=None, main_stat=None, for_archetype=None):

    # determine armor class first
    armor_class = armor_class or (constants.ARMOR_CLASS_HEAVY
                                  if roll_percentage(25)
                                  else constants.ARMOR_CLASS_LIGHT)

    if quality == constants.ITEM_QUALITY_NORMAL:
        stats = {}
        main_stat = None
    else:

        # We don't want any Int-related stats if the armor is heavy
        exclude_stats = []
        if armor_class == constants.ARMOR_CLASS_HEAVY:
            exclude_stats = [
                constants.ATTR_INT,
                constants.ATTR_MAX_MANA,
                constants.ATTR_REGEN_MANA,
                constants.ATTR_SP,
            ]

        stats = generate_boosted_stats(
            level=level,
            eq_type=eq_type,
            quality=quality,
            exclude_stats=exclude_stats,
            main_stat=main_stat,
            for_archetype=for_archetype)

    # determine name
    name = naming.name_armor(
        eq_type=eq_type,
        level=level,
        armor_class=armor_class,
        quality=quality,
        stats=stats)

    stats['name'] = name
    stats['armor_class'] = armor_class
    stats['equipment_type'] = eq_type

    return stats


def generate_shield(level, quality, armor_class=None, main_stat=None, for_archetype=None):
    """
    Shields are generated as such:
    * If normal, 40% heavy 60% light
    * If magic, pick one of 5 primary stat / armor combinations

    This is a different approach than for armor, where we let all combinations
    play out except but specifically restrict int / mana when heavy.
    """
    if quality == constants.ITEM_QUALITY_NORMAL:
        armor_class = armor_class or (constants.ARMOR_CLASS_HEAVY
                                  if roll_percentage(40)
                                  else constants.ARMOR_CLASS_LIGHT)
        stats = {}
    else:
        if armor_class and main_stat:
            pass
        elif not armor_class and not main_stat:
            buckets = [
                { # light int
                    'armor_class': constants.ARMOR_CLASS_LIGHT,
                    'stat': constants.ATTR_INT,
                },
                { # light dex
                    'armor_class': constants.ARMOR_CLASS_LIGHT,
                    'stat': constants.ATTR_DEX,
                },
                { # light con
                    'armor_class': constants.ARMOR_CLASS_LIGHT,
                    'stat': constants.ATTR_CON,
                },
                { # heavy con
                    'armor_class': constants.ARMOR_CLASS_HEAVY,
                    'stat': constants.ATTR_CON,
                },
                { # heavy str
                    'armor_class': constants.ARMOR_CLASS_HEAVY,
                    'stat': constants.ATTR_STR,
                },
            ]
            bucket = random.choice(buckets)
            armor_class = bucket['armor_class']
            main_stat = bucket['stat']
        elif armor_class:
            if armor_class == constants.ARMOR_CLASS_LIGHT:
                main_stat = random.choice(
                    [constants.DEX, constants.INT, constants.CON])
            else:
                main_stat = [constants.STR, constants.CON]
        elif main_stat:
            if main_stat == constants.STR:
                armor_class = constants.ARMOR_CLASS_HEAVY
            elif main_stat == constants.CON:
                armor_class = random.choice(
                    [constants.ARMOR_CLASS_HEAVY, constants.ARMOR_CLASS_LIGHT])
            else:
                armor_class = constants.ARMOR_CLASS_LIGHT
        # Generate the boosted stats
        stats = generate_boosted_stats(
            level=level,
            quality=quality,
            eq_type=constants.EQUIPMENT_TYPE_SHIELD,
            main_stat=main_stat,
            for_archetype=None)

    name = naming.name_shield(
        level=level,
        armor_class=armor_class,
        quality=quality,
        stats=stats)

    stats['name'] = name
    stats['armor_class'] = armor_class
    stats['equipment_type'] = constants.EQUIPMENT_TYPE_SHIELD
    return stats


def generate_weapon(level, quality, eq_type, main_stat=None, for_archetype=None):
    if quality == constants.ITEM_QUALITY_NORMAL:
        stats = {}
    else:
        stats = generate_boosted_stats(
            level=level,
            eq_type=eq_type,
            quality=quality,
            main_stat=main_stat,
            for_archetype=None)

    weapon_data = naming.name_weapon(
        eq_type=eq_type,
        level=level,
        quality=quality,
        stats=stats)

    stats['name'] = weapon_data['name']
    stats['equipment_type'] = eq_type
    stats['hit_msg_first'] = weapon_data['hit_msg_first']
    stats['hit_msg_third'] = weapon_data['hit_msg_third']
    stats['keywords'] = weapon_data['keywords']
    stats['equipment_type'] = eq_type
    if weapon_data.get('weapon_type'):
        stats['weapon_type'] = weapon_data['weapon_type']
    return stats
