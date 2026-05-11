"""
Module for computing character stats.
"""
import math

from config import constants
from config import game_settings as config
from core.stat_system import compute_stats

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
