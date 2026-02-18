import copy
import math
import random

from config import constants

"""
10 Points - CON, STR, DEX, INT, 1 Mana Per Tick
40 Points - 1 Health Per Tick
20 Points - 1 Mana Per Tick
4 Points - Resilience, Dodge Rating, Crit Rating, Attack Power, Spell Power, 1 Health
2 Points - 1 Mana
"""


def ceil_with_factor(number, factor):
    return math.ceil(number / factor) * factor

def floor_with_factor(number, factor):
    return math.floor(number / factor) * factor

def roll_attribute_in_range(budget, attr, min, max):
    """
    For a given budget, rolls a random number of the desired attribute within
    a certain range of use in the budget. For example, how many points of
    strength should I have for a level 3 large item (budget 167)?
    between 9 and 13.
    """
    cost = constants.ATTR_BUDGET[attr]
    min_attr_points = int(ceil_with_factor(budget * min, cost) / cost)
    max_attr_points = int(floor_with_factor(budget * max, cost) / cost)
    if min_attr_points == max_attr_points:
        return min_attr_points
    elif min_attr_points > max_attr_points:
        return min_attr_points
    return random.choice(range(min_attr_points, max_attr_points))


# ==== Spending ====

def spend_budget(budget, primary_attr=None, secondary_attr=None, min=0.5, max=0.8, compute_secondary=True, exclude_stats=None):
    """
    Spend a range of a given budget on a primary stat, and the rest on a
    secondary stat. The selected budget for the primary stat ensures that it
    is a multiple of the attribute cost in between the min and the max %.
    """
    attrs = []

    PRIMARY_ATTRS = copy.copy(constants.PRIMARY_ATTRIBUTES)
    ATTRS = copy.copy(constants.ATTRIBUTES)
    # Exclude stamina max and regen from pool of attributes
    ATTRS.remove(constants.ATTR_MAX_STAMINA)
    ATTRS.remove(constants.ATTR_REGEN_STAMINA)

    if exclude_stats:
        PRIMARY_ATTRS = [
            s for s in PRIMARY_ATTRS
            if s not in exclude_stats
        ]
        ATTRS = [
            s for s in ATTRS
            if s not in exclude_stats
        ]

    if not primary_attr:
        primary_attr = random.choice(PRIMARY_ATTRS)
    primary_attr_cost = constants.ATTR_BUDGET[primary_attr]
    primary_attr_num = roll_attribute_in_range(budget, primary_attr, min, max)
    budget_used = primary_attr_cost * primary_attr_num
    attrs.append({
        'name': primary_attr,
        'points': primary_attr_num,
        'budget_used': budget_used,
    })

    if not compute_secondary:
        return attrs

    if not secondary_attr:
        secondary_attr = random.choice(ATTRS)
    secondary_attr_cost = constants.ATTR_BUDGET[secondary_attr]
    budget_remaining = budget - budget_used
    secondary_attr_num = math.floor((budget - budget_used) / secondary_attr_cost)
    secondary_budget_used = secondary_attr_cost * secondary_attr_num
    attrs.append({
        'name': secondary_attr,
        'points': secondary_attr_num,
        'budget_used': secondary_budget_used,
    })

    return attrs

# ==== Generation ====



# ====