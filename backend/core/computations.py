"""
Module for computing character stats
"""
import math

from config import constants
from config import game_settings as config

def compute_stats(level, archetype=None, char=None, boost_mob=False, is_mob=False, faction_level=0):
    """
    Compute a character's attributes based on their stats, eq and level.

    This function gets invoked by the game engine and the data definition
    layer

    is_mob sounds reundant because inspecting char's class would say
    whether or not it's a mob but for some of the estimation functions,
    we don't actually have a character object yet, just an item template,
    so then char is None and is_mob is True.
    """

    stats = {
        'strength': 0,
        'constitution': 0,
        'dexterity': 0,
        'intelligence': 0,

        'armor': 0,
        'crit': 0,
        'dodge': 0,
        'resilience': 0,

        'health_max': 0,
        'mana_max': 0,
        'stamina_max': config.PLAYER_STARTING_MAX_STAMINA,

        'attack_power': 0,
        'spell_power': 0,

        'mana_regen': 0,
        'health_regen': 0,
        'stamina_regen': 0,
    }

    if archetype == constants.ARCHETYPE_WARRIOR:
        primary_stat = 'strength'
        stats_constants = {
            'constitution': 3,
            'strength': 4,
            'dexterity': 1,
            'intelligence': 1,
        }
    elif archetype == constants.ARCHETYPE_ASSASSIN:
        primary_stat = 'dexterity'
        stats_constants = {
            'constitution': 3,
            'strength': 1,
            'dexterity': 4,
            'intelligence': 1,
        }
    elif archetype in (constants.ARCHETYPE_MAGE, constants.ARCHETYPE_CLERIC):
        primary_stat = 'intelligence'
        stats_constants = {
            'constitution': 3,
            'strength': 1,
            'dexterity': 1,
            'intelligence': 4,
        }
    else:
        stats_constants = {
            'constitution': 3,
            'strength': 2,
            'dexterity': 2,
            'intelligence': 2,
        }

    for stat, value in stats_constants.items():
        stats[stat] = math.ceil(config.ILF(level) * value)

    # Before adding equipment, record mana and int base without it so that we can
    # use that in spells calculation and grant base mana (equivalent to double base INT)
    int_base = stats['intelligence']
    mana_base = int_base * 2
    stats['mana_base'] = mana_base
    stats['mana_max'] = mana_base

    stats['health_base'] = stats['constitution'] * 2 + stats['strength']
    stats['stamina_base'] = config.PLAYER_STARTING_MAX_STAMINA

    # Minor faction boost

    if faction_level:
        for stat in constants.PRIMARY_ATTRIBUTES:
           stats[stat] *= 1 + (faction_level * config.FACTION_STAT_BONUS / 100)


    # Augment with equipment
    if char:
        for item in char.equipment:
            item.lazy = True
            augment = item.augment
            for stat in stats:
                stats[stat] += getattr(item, stat, 0)
                # Factor in augments
                if augment:
                    stats[stat] += getattr(augment, stat, 0)


    if boost_mob:
        # Because mobs don't come fully equipped therefore we assume that
        # they're wearing a full set of imbued gear, which would give
        # them a 10.25 combined slot factor.
        sfactor = 10.25
        stats_boost = math.ceil(config.ILF(level) * sfactor)

        if boost_mob == 'elite':
            stats_boost *= 1.2

        # Give half those stats in con
        con_stats = round(stats_boost / 2)
        stats['constitution'] += con_stats
        stats_boost -= con_stats

        # Depending on the class, give the rest all in the desired slot
        if archetype:
            stats[primary_stat] += stats_boost
        else:
            for primary_stat in ['strength', 'intelligence', 'dexterity']:
                stats[primary_stat] += stats_boost

        # Give some armor to mobs, who naturally have none
        stats['armor'] = math.ceil(10.25 * config.ILF(level))

        # If the mob is a warrior, give it almost heavy armor
        # (x3 instead of x4)
        if archetype == constants.ARCHETYPE_WARRIOR:
            stats['armor'] *= 3
        elif not archetype:
            stats['armor'] *= 2

    # Con boost
    stats['health_max'] += stats['constitution'] * 2
    stats['resilience'] += stats['constitution']
    # Str boost
    stats['attack_power'] += stats['strength']
    stats['health_max'] += stats['strength'] * 1
    if archetype == constants.ARCHETYPE_WARRIOR:
        stats['crit'] += stats['strength']
    # Int boost
    stats['spell_power'] += stats['intelligence'] * 2
    stats['mana_max'] += stats['intelligence'] - int_base
    # Dex boost
    stats['dodge'] += stats['dexterity']
    stats['crit'] += stats['dexterity']
    if archetype == constants.ARCHETYPE_ASSASSIN:
        stats['attack_power'] += stats['dexterity']

    # AP / SP boost if using a 2h weapon or if setting up a mob
    if (boost_mob or
        char and
        char.weapon and
        char.weapon.equipment_type == constants.EQUIPMENT_TYPE_WEAPON_2H
        or boost_mob):
            stats['spell_power'] = math.ceil(stats['spell_power'] * 1.5)
            stats['attack_power'] = math.ceil(stats['attack_power'] * 1.5)

    # For each stat, make sure none of the values are negative
    for stat in stats:
        stats[stat] = max(0, stats[stat])

    return stats

def scaled_formula(char, enemy, stat):
    """
    Returns a probability float, for example 0.02 for 2%

    # Armor, Dodge, Resilience: Value = (X + L * K * C) / (X + L * K)
    # Crit: Value = X / (L * K) + C

    Future mount implementation notes:
    * mount-based level modifiers will have to be multiplicative instead of
      additive to keep the scaling consistent as levels go up
    * I'm mounted and you're not. When calculating my crit chance, we use my
      crit rating and your level * (1 - X). When calculating your armor
      mitigation, we use your armor and my level * (1 + X)
    """

    # Save laziness so we can revert to whatever it was aftereards
    laziness = char.lazy
    char.lazy = True

    if stat == 'armor':
        constant = 60
        base = 0
        value = char.armor

        if enemy:
            value = enemy.apply_feats_for_type(constants.FEAT_TYPE_ENEMY_ARMOR, value)
    elif stat == 'dodge':
        constant = 60
        base = 0.02
        value = char.dodge
    elif stat == 'crit':
        constant = 120
        base = 0.02
        value = char.crit
    elif stat == 'resilience':
        constant = 120
        base = 0
        value = char.resilience
        # If the character has a shield equipped, set the base resilience
        # to 0.25
        if (char.offhand and
            char.offhand.equipment_type == constants.EQUIPMENT_TYPE_SHIELD):
            base = 0.25

        base = char.apply_feats_for_type(constants.FEAT_TYPE_BASE_RESILIENCE, base)
    else:
        raise ValueError("Invalid stat: %s" % stat)

    # So that we can return estimates, we take the character's level if the
    # enemy is None to return what the value would be against an opponent
    # of a same level.
    enemy_level = enemy.level if enemy else char.level

    enemy_ilf = config.ILF(enemy_level)

    # Revert to original laziness
    char.lazy = laziness


    if stat == 'crit':
        return min(1.0, value / (enemy_ilf * constant) + base)

    numerator = value + enemy_ilf * constant * base
    denominator = value + enemy_ilf * constant

    # Cap mitigation to 75% for dodge, armor, and resilience
    return min(0.75, numerator / denominator)
