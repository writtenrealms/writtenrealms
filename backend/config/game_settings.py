import os


# Keep this aligned with environment-specific runtime settings.
INSTANCE_ID = os.environ.get('INSTANCE_ID')


def pick(config):
    return config.get(INSTANCE_ID, config.get('default'))


IS_CLUSTER = pick({
    'default': False,
    'k8s': True,
    'prod': True,
    'ptr': True,
    'local': True,
})


# ==== Game settings used by backend ====

MOVEMENT_COST = 2
# Shared cadence for heartbeat/tick-like async behavior across WR2 systems.
GAME_HEARTBEAT_INTERVAL_SECONDS = 2

LEVEL_EXPERIENCE = [
    0,      # 1
    30,     # 2
    100,    # 3
    400,    # 4
    1000,   # 5
    2500,   # 6
    5500,   # 7
    10000,  # 8
    15000,  # 9
    25000,  # 10
    40000,  # 11
    55000,  # 12
    75000,  # 13
    100000, # 14
    135000, # 15
    175000, # 16
    225000, # 17
    285000, # 18
    370000, # 19
    500000, # 20
]

# How much exp a mob gives per level
MOB_EXP = {
    1:   16,
    2:   25,
    3:   40,
    4:   61,
    5:   88,
    6:   121,
    7:   160,
    8:   205,
    9:   256,
    10:  313,
    11:  376,
    12:  445,
    13:  520,
    14:  601,
    15:  688,
    16:  781,
    17:  880,
    18:  985,
    19:  1096,
    20:  1213,
}

ELITE_BOOST_DEFAULT = 4
ELITE_BOOST = {'armor': 1, 'resilience': 1, 'health_max': 6}

CRAFTER_MULTIPLIER = 3
MERCHANT_PROFITS = 2

PLAYER_STARTING_MAX_STAMINA = 100

FLEX_SKILL_LEVEL_1 = 6
FLEX_SKILL_LEVEL_2 = 10
FLEX_SKILL_LEVEL_3 = 14

FACTION_STAT_BONUS = 5  # percents


def ILF(level):
    if level < 17:
        return 1.1 ** level * 5.5

    # start at 16
    value = 1.1 ** 16 * 5.5

    if level >= 17:
        value *= 1.08
    if level >= 18:
        value *= 1.06
    if level >= 19:
        value *= 1.04
    if level >= 20:
        value *= 1.02
    return value

# When generating stats for procedurally generated items, how much to
# vary the value by. In percentage points.
RANDOM_ROLL_VARIANCE = 16
