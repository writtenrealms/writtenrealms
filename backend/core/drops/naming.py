import random

from backend.config import constants
from backend.core.utils import roll_percentage

# ===== Armor data =====

BASIC_LIGHT_ARMOR = {
    constants.EQUIPMENT_TYPE_HEAD: 'a {adj}leather hood',
    constants.EQUIPMENT_TYPE_BODY: 'a {adj}leather vest',
    constants.EQUIPMENT_TYPE_ARMS: 'a set of {adj}leather coverings',
    constants.EQUIPMENT_TYPE_HANDS: 'a pair of {adj}leather gloves',
    constants.EQUIPMENT_TYPE_WAIST: 'a {adj}leather belt',
    constants.EQUIPMENT_TYPE_LEGS: 'a set of {adj}leather leggings',
    constants.EQUIPMENT_TYPE_FEET: 'a pair of {adj}leather boots',
}

BASIC_HEAVY_ARMOR = {
    constants.EQUIPMENT_TYPE_HEAD: 'a {adj}metal helmet',
    constants.EQUIPMENT_TYPE_BODY: 'a {adj}metal breastplate',
    constants.EQUIPMENT_TYPE_ARMS: 'a pair of {adj}metal vambraces',
    constants.EQUIPMENT_TYPE_HANDS: 'a pair of {adj}metal gauntlets',
    constants.EQUIPMENT_TYPE_WAIST: 'a {adj}metal belt',
    constants.EQUIPMENT_TYPE_LEGS: 'a set of {adj}metal greaves',
    constants.EQUIPMENT_TYPE_FEET: 'a pair of {adj}metal boots',
}

BASIC_EQ = {
    constants.ARMOR_CLASS_LIGHT: BASIC_LIGHT_ARMOR,
    constants.ARMOR_CLASS_HEAVY: BASIC_HEAVY_ARMOR,
}

MAGIC_ADJECTIVES = { # Notice the trailing spaces
    constants.ATTR_CON: {
        constants.ARMOR_CLASS_LIGHT: 'durable ',
        constants.ARMOR_CLASS_HEAVY: 'sturdy '
    },
    constants.ATTR_INT: {
        constants.ARMOR_CLASS_LIGHT: 'woven ',
        constants.ARMOR_CLASS_HEAVY: 'cracked '
    },
    constants.ATTR_DEX: {
        constants.ARMOR_CLASS_LIGHT: 'supple ',
        constants.ARMOR_CLASS_HEAVY: 'dented '
    },
    constants.ATTR_STR: {
        constants.ARMOR_CLASS_LIGHT: 'worn ',
        constants.ARMOR_CLASS_HEAVY: 'polished '
    },
}

LEVEL_ADJECTIVES = { # Notice the trailing spaces
    'first_tier': {
        constants.ARMOR_CLASS_LIGHT: 'patched ',
        constants.ARMOR_CLASS_HEAVY: 'rusty '
    },
    'second_tier': {
        constants.ARMOR_CLASS_LIGHT: 'cracked ',
        constants.ARMOR_CLASS_HEAVY: ''
    },
    'third_tier': {
        constants.ARMOR_CLASS_LIGHT: '',
        constants.ARMOR_CLASS_HEAVY: 'thick '
    },
    'fourth_tier': {
        constants.ARMOR_CLASS_LIGHT: 'reinforced ',
        constants.ARMOR_CLASS_HEAVY: 'stained '
    },
}

SPECIAL_SETS = {
    constants.ARMOR_CLASS_LIGHT: {
        constants.ATTR_INT: {
            constants.EQUIPMENT_TYPE_HEAD: 'a dark gray cloth hood',
            constants.EQUIPMENT_TYPE_BODY: 'a long flowing robe',
            constants.EQUIPMENT_TYPE_ARMS: 'a set of thin plate vambraces',
            constants.EQUIPMENT_TYPE_HANDS: 'a pair of silk gloves',
            constants.EQUIPMENT_TYPE_WAIST: 'a dark gray sash',
            constants.EQUIPMENT_TYPE_LEGS: 'a set of thin plate shin guards',
            constants.EQUIPMENT_TYPE_FEET: 'a pair of simple slippers',
        },
        constants.ATTR_DEX: {
            constants.EQUIPMENT_TYPE_HEAD: 'a hood with a face mask',
            constants.EQUIPMENT_TYPE_BODY: 'a leather jerkin',
            constants.EQUIPMENT_TYPE_ARMS: 'a set of finely-stiched leather cuffs',
            constants.EQUIPMENT_TYPE_HANDS: 'a pair of worn leather grips',
            constants.EQUIPMENT_TYPE_WAIST: 'a thin weapons belt',
            constants.EQUIPMENT_TYPE_LEGS: 'a pair of stretchy black pants',
            constants.EQUIPMENT_TYPE_FEET: 'a pair of buckled black boots',
        },
        constants.ATTR_CON: {
            constants.EQUIPMENT_TYPE_HEAD: 'a chainmail coif',
            constants.EQUIPMENT_TYPE_BODY: 'a tunic of riveted chainmail',
            constants.EQUIPMENT_TYPE_ARMS: 'a set of chainmail coverings',
            constants.EQUIPMENT_TYPE_HANDS: 'a pair of metal half-gauntlets',
            constants.EQUIPMENT_TYPE_WAIST: 'a thick leather belt',
            constants.EQUIPMENT_TYPE_LEGS: 'a pair of thick leather greaves',
            constants.EQUIPMENT_TYPE_FEET: 'a pair of metal footguards',
        },
    },
    constants.ARMOR_CLASS_HEAVY: {
        constants.ATTR_STR: {
            constants.EQUIPMENT_TYPE_HEAD: 'a spiked metal helmet',
            constants.EQUIPMENT_TYPE_BODY: 'a breastplate with spiked shoulders',
            constants.EQUIPMENT_TYPE_ARMS: 'a set of vambraces with spiked elbows',
            constants.EQUIPMENT_TYPE_HANDS: 'a pair of steel-knuckled gauntlets',
            constants.EQUIPMENT_TYPE_WAIST: 'a bronze-plated leather belt',
            constants.EQUIPMENT_TYPE_LEGS: 'a pair of greaves with spiked knees',
            constants.EQUIPMENT_TYPE_FEET: 'a pair of steel toe boots',
        },
        constants.ATTR_CON: {
            constants.EQUIPMENT_TYPE_HEAD: 'a shining steel helmet',
            constants.EQUIPMENT_TYPE_BODY: 'a burnished steel breastplate',
            constants.EQUIPMENT_TYPE_ARMS: 'a set of steel vambraces',
            constants.EQUIPMENT_TYPE_HANDS: 'a pair of thick steel gauntlets',
            constants.EQUIPMENT_TYPE_WAIST: 'a steel-plated leather belt',
            constants.EQUIPMENT_TYPE_LEGS: 'a pair of steel-plated greaves',
            constants.EQUIPMENT_TYPE_FEET: 'a pair of thick steel boots',
        },
    },
}

# ===== Shield data =====

NORMAL_SHIELD_NAMES = {
    constants.ARMOR_CLASS_LIGHT: 'a wooden buckler',
    constants.ARMOR_CLASS_HEAVY: 'a large metal shield',
}

MAGIC_SHIELD_NAMES = {
    constants.ARMOR_CLASS_LIGHT: {
        constants.ATTR_INT: 'a bright reflective shield',
        constants.ATTR_DEX: 'a light shield covered in leather',
        constants.ATTR_CON: 'a round steel-plated shield',
    },
    constants.ARMOR_CLASS_HEAVY: {
        constants.ATTR_STR: 'a spiked metal shield',
        constants.ATTR_CON: 'a large kite shield',
    },
}

# ===== Weapons data =====

NORMAL_WEAPONS = {
    constants.EQUIPMENT_TYPE_WEAPON_1H: [
        {
            'name': 'a chipped shortsword',
            'hit_msg_first': 'slash',
            'hit_msg_third': 'slashes',
            'keywords': 'shortsword sword chipped short',
            'weapon_type': 'sword',
        },
    ],
    constants.EQUIPMENT_TYPE_WEAPON_2H: [
        {
            'name': 'a heavy longsword',
            'hit_msg_first': 'smite',
            'hit_msg_third': 'smites',
            'keywords': 'longsword sword long heavy',
            'weapon_type': 'sword',
        }
    ],
}

MAGIC_WEAPONS = {
    constants.EQUIPMENT_TYPE_WEAPON_1H: {
        constants.ATTR_CON: [
            {
                'name': 'a bronze-flanged mace',
                'hit_msg_first': 'pound',
                'hit_msg_third': 'pounds',
                'keywords': 'mace bronze flanged',
                'weapon_type': 'mace',
            },
            {
                'name': 'a razor-sharp mattock',
                'hit_msg_first': 'crush',
                'hit_msg_third': 'crushes',
                'keywords': 'mattock razor sharp',
                'weapon_type': 'polearm',
            },
        ],
        constants.ATTR_STR: [
            {
                'name': 'a honed broadsword',
                'hit_msg_first': 'slash',
                'hit_msg_third': 'slashes',
                'keywords': 'broadsword sword honed broad',
                'weapon_type': 'sword',
            },
            {
                'name': 'a leather-hilted battleaxe',
                'hit_msg_first': 'scythe',
                'hit_msg_third': 'scythes',
                'keywords': 'battleaxe axe battle leather hilted',
                'weapon_type': 'axe',
            },
        ],
        constants.ATTR_INT: [
            {
                'name': 'a carved oaken staff',
                'hit_msg_first': 'strike',
                'hit_msg_third': 'strikes',
                'keywords': 'staff carved oak oaken',
                'weapon_type': 'staff',
            },
            {
                'name': 'a staff of twisted sycamore',
                'hit_msg_first': 'strike',
                'hit_msg_third': 'strikes',
                'keywords': 'staff twisted sycamore',
                'weapon_type': 'staff',
            },
        ],
        constants.ATTR_DEX: [
            {
                'name': 'a glistening scimitar',
                'hit_msg_first': 'slice',
                'hit_msg_third': 'slices',
                'keywords': 'scimitar glistening sword',
                'weapon_type': 'sword',
            },
            {
                'name': 'a sharp curved dagger',
                'hit_msg_first': 'pierce',
                'hit_msg_third': 'pierces',
                'keywords': 'dagger sharp curved',
                'weapon_type': 'dagger',
            },
        ],
    },
    constants.EQUIPMENT_TYPE_WEAPON_2H: {
        constants.ATTR_CON: [
            {
                'name': 'a brutal war maul',
                'hit_msg_first': 'blast',
                'hit_msg_third': 'blasts',
                'keywords': 'maul brutal war club',
                'weapon_type': 'club',
            },
        ],
        constants.ATTR_STR: [
            {
                'name': 'a gleaming claymore',
                'hit_msg_first': 'slice',
                'hit_msg_third': 'slices',
                'keywords': 'claymore gleaming sword',
                'weapon_type': 'sword',
            },
        ],
        constants.ATTR_INT: [
            {
                'name': 'a faintly glowing greatstaff',
                'hit_msg_first': 'strike',
                'hit_msg_third': 'strikes',
                'keywords': 'greatstaff staff faintly glowing',
                'weapon_type': 'staff',
            },
        ],
        constants.ATTR_DEX: [
            {
                'name': 'a spear with a steel-bladed tip',
                'hit_msg_first': 'pierce',
                'hit_msg_third': 'pierces',
                'keywords': 'spear steel bladed',
                'weapon_type': 'spear',
            },
        ],
    }
}

# Revamped Descriptions

REVAMP_LEVEL_THRESHOLD = 10 #Transition to new system at higher levels

ARMOR_NAMES = {
    constants.ARMOR_CLASS_LIGHT: {
        # 20% chance of metal item
        constants.EQUIPMENT_TYPE_HEAD: {
            constants.ITEM_NAME_TYPE_CLOTH: ['hat', 'hood', 'headband', 'cap',
                                             'bonnet', 'veil'],
            constants.ITEM_NAME_TYPE_METAL: ['coronet', 'mask', 'crown',
                                             'tiara', 'diadem'],
        },
        constants.EQUIPMENT_TYPE_BODY: { # space prefix indicates "a set of"
            constants.ITEM_NAME_TYPE_CLOTH: ['vest', 'tunic', 'jerkin', 'cloak',
                                             'robe', 'shroud', 'silks', 'coat',
                                             'pelt', 'mantle', 'tabard', 'wrap',
                                             'doublet', ' vestments', ' hides'],
            constants.ITEM_NAME_TYPE_METAL: [' chainmail', ' ringmail'],
        },
        constants.EQUIPMENT_TYPE_ARMS: {
            constants.ITEM_NAME_TYPE_CLOTH: ['sleeves', 'coverings', 'armbands',
                                             'wristbands', 'cuffs', 'wraps'],
            constants.ITEM_NAME_TYPE_METAL: ['bracelets', 'shackles'],
        },
        constants.EQUIPMENT_TYPE_HANDS: {
            constants.ITEM_NAME_TYPE_CLOTH: ['gloves', 'mitts', 'grips'],
            constants.ITEM_NAME_TYPE_METAL: ['handguards'],
        },
        # 0% chance of metal item if none in dictionary
        constants.EQUIPMENT_TYPE_WAIST: {
            constants.ITEM_NAME_TYPE_CLOTH: ['belt', 'sash', 'cincture', 'cord',
                                             'wrap', 'baldric'],
        },
        constants.EQUIPMENT_TYPE_LEGS: {
            constants.ITEM_NAME_TYPE_CLOTH: ['trousers', 'breeches', 'leggings',
                                             'pants', 'stockings', 'legwraps'],
            constants.ITEM_NAME_TYPE_METAL: ['chausses', 'shinguards'],
        },
        constants.EQUIPMENT_TYPE_FEET: {
            constants.ITEM_NAME_TYPE_CLOTH: ['boots', 'slippers', 'shoes',
                                             'treads', 'moccasins', 'sandals'],
        },
    },
    # all heavy armor is metal
    constants.ARMOR_CLASS_HEAVY: {
        constants.EQUIPMENT_TYPE_HEAD: ['helmet', 'helm', 'coif', 'faceguard',
                                        'armet', 'headgear', 'basinet',
                                        'casque', 'stechhelm'],
        constants.EQUIPMENT_TYPE_BODY: ['breastplate', 'hauberk', 'brigandine',
                                        'chestplate', 'cuirass', ' plate',
                                        ' platemail', ' half plate',
                                        ' full plate', ' splint mail'],
        constants.EQUIPMENT_TYPE_ARMS: ['vambraces', 'bracers', 'armguards',
                                        'armplates', 'wristplates',
                                        'deflectors', 'wristguards'],
        constants.EQUIPMENT_TYPE_HANDS: ['gauntlets', 'fists', 'clutches',
                                         'manifers'],
        constants.EQUIPMENT_TYPE_WAIST: ['bucklet', 'girdle', 'coil', 'binding',
                                         'harness'],
        constants.EQUIPMENT_TYPE_LEGS: ['greaves', 'faulds', 'cuisses',
                                        'tassets', 'poleyns', 'legplates',
                                        'schynbalds', 'legguards'],
        constants.EQUIPMENT_TYPE_FEET: ['sabatons', 'footguards', 'brogans'],
    },
}

PAIR_ARMOR_TYPES = [constants.EQUIPMENT_TYPE_ARMS,
                    constants.EQUIPMENT_TYPE_HANDS,
                    constants.EQUIPMENT_TYPE_LEGS,
                    constants.EQUIPMENT_TYPE_FEET]
PAIR_NAMES = ['set', 'pair']

SHIELD_NAMES = {
    constants.ARMOR_CLASS_LIGHT: ['buckler', 'targe', 'rondache', 'guard',
                                  'pelta', 'aspis', 'hoplon'],
    constants.ARMOR_CLASS_HEAVY: ['shield', 'scutum', 'pavise', 'barrier',
                                  'kite shield', 'tower shield', 'ward',
                                  'heater shield'],
}

WEAPON_NAMES = {
    constants.EQUIPMENT_TYPE_WEAPON_1H: {
        constants.WEAPON_TYPE_SWORD: {
            'messages': {
                'hit_msg_first': 'slash',
                'hit_msg_third': 'slashes',
            },
            'names': ['short sword', 'scimitar', 'sabre', 'long sword',
                      'broad sword', 'cutlass', 'shamshir'],
        },
        constants.WEAPON_TYPE_AXE: {
            'messages': {
                'hit_msg_first': 'slice',
                'hit_msg_third': 'slices',
            },
            'names':  ['hand axe', 'war axe', 'hatchet', 'cleaver',
                       'broad axe'],
        },
        constants.WEAPON_TYPE_CLUB: {
            'messages': {
                'hit_msg_first': 'pummel',
                'hit_msg_third': 'pummels',
            },
            'names': ['club', 'mace', 'shillelagh', 'cudgel', 'truncheon'],
        },
        constants.WEAPON_TYPE_SPEAR: {
            'messages': {
                'hit_msg_first': 'stab',
                'hit_msg_third': 'stabs',
            },
            'names':  ['spear', 'glaive', 'spetum'],
        },
        constants.WEAPON_TYPE_STAFF: {
            'messages': {
                'hit_msg_first': 'smack',
                'hit_msg_third': 'smacks',
            },
            'names':  ['scepter', 'rod', 'short staff', 'baton'],
        },
        constants.WEAPON_TYPE_DAGGER: {
            'messages': {
                'hit_msg_first': 'stab',
                'hit_msg_third': 'stabs',
            },
            'names':  ['dagger', 'dirk', 'kris',  'stilleto', 'kukri'],
        }
    },
    constants.EQUIPMENT_TYPE_WEAPON_2H: {
        constants.WEAPON_TYPE_SWORD: {
            'messages': {
                'hit_msg_first': 'smash',
                'hit_msg_third': 'smashes',
            },
            'names': ['claymore', 'bastard sword', 'great sword', 'flamberge',
                      'zweihander'],
        },
        constants.WEAPON_TYPE_AXE: {
            'messages': {
                'hit_msg_first': 'sunder',
                'hit_msg_third': 'sunders',
            },
            'names': ['battle axe', 'great axe', 'long-bearded axe',
                      'double axe', 'twin axe'],
        },
        constants.WEAPON_TYPE_CLUB: {
            'messages': {
                'hit_msg_first': 'crush',
                'hit_msg_third': 'crushes',
            },
            'names':  ['maul', 'war hammer', 'mallet', 'morning star'],
        },
        constants.WEAPON_TYPE_SPEAR: {
            'messages': {
                'hit_msg_first': 'pierce',
                'hit_msg_third': 'pierces',
            },
            'names':  ['lance', 'pike', 'trident'],
        },
        constants.WEAPON_TYPE_STAFF: {
            'messages': {
                'hit_msg_first': 'smite',
                'hit_msg_third': 'smites',
            },
            'names':  ['staff', 'greatstaff',  'quarterstaff', 'walking stick'],
        },
        constants.WEAPON_TYPE_POLEARM: {
            'messages': {
                'hit_msg_first': 'swing down on',
                'hit_msg_third': 'swings down on',
            },
            'names':  ['poleaxe', 'halberd', 'scythe'],
        },
    },
}

ITEM_DESCRIPTORS = {
    constants.ITEM_NAME_TYPE_CLOTH: {
        constants.ATTR_CON: ['a strong', 'an artisanal', 'an exquisite',
                             'an embroidered', 'a reinforced'],
        constants.ATTR_DEX: ['a crimson', 'a smooth', 'a fine-stitched',
                             'a delicate', 'a dyed'],
        constants.ATTR_INT: ['a shimmering', 'a regal', 'a fine', 'a luxurious',
                             'an azure'],
    },
    constants.ITEM_NAME_TYPE_METAL: {
        constants.ATTR_CON: ['a bronze', 'a mithril-lined', 'a steel',
                             'a stout', 'a splendid'],
        constants.ATTR_DEX: ['a ridged', 'a burnished', 'a gleaming',
                             'a polished'],
        constants.ATTR_INT: ['an ornate', 'a shining', 'a gem-encrusted',
                             'a runic'],
        constants.ATTR_STR: ['a russet', 'a well-crafted', 'a spiked',
                             'an iron'],
    },
    # Any descriptor starting with a space is used as a suffix
    # To support this, all weapon names must currently begin with a consonant
    constants.ITEM_NAME_TYPE_BLUNT: {
        constants.ATTR_CON: ['a massive', 'a grand', 'a hefty', 'a colossal'],
        constants.ATTR_DEX: ['a brutal', 'a spiked', 'a lightweight'],
        constants.ATTR_INT: ['an elegant', 'a fine', ' engraved with runes'],
        constants.ATTR_STR: ['a savage', 'a vicious', 'an imposing'],
    },
    constants.ITEM_NAME_TYPE_BLADE: {
        constants.ATTR_CON: ['a superior', 'a massive', 'an enormous',
                             ' with a heavy pommel'],
        constants.ATTR_DEX: ['a honed', 'a sharp', ' with a thin blade'],
        constants.ATTR_INT: ['an elegant', 'a fine', ' with a gemmed pommel'],
        constants.ATTR_STR: ['a jagged', 'a barbed', ' with a serrated edge',
                             ' with a steel blade']
    },
    constants.ITEM_NAME_TYPE_CASTER: {
        constants.ATTR_INT: ['a faintly glowing', ' adorned with feathers',
                             'a luminous'],
        constants.ATTR_CON: ['a bejeweled', 'a brilliant'],
    },
}



def name_armor(level, eq_type, armor_class, quality, stats):

    # Start with the basics
    basic_eq_unfmt_str = BASIC_EQ[armor_class][eq_type]
    adj = ''

    if quality == constants.ITEM_QUALITY_NORMAL:
        if level <= 5:
            adj = LEVEL_ADJECTIVES['first_tier']
        elif level <= 10:
            adj = LEVEL_ADJECTIVES['second_tier']
        elif level <= 15:
            adj = LEVEL_ADJECTIVES['third_tier']
        else:
            adj = LEVEL_ADJECTIVES['fourth_tier']
        adj = adj[armor_class]

    else:
        main_stat = item_utils.get_main_primary_stat(stats)

        if level > REVAMP_LEVEL_THRESHOLD:

            if armor_class == constants.ARMOR_CLASS_HEAVY:
                item_type = constants.ITEM_NAME_TYPE_METAL
                names = ARMOR_NAMES[armor_class][eq_type]
            else:
                item_type = constants.ITEM_NAME_TYPE_CLOTH
                slot_info = ARMOR_NAMES[armor_class][eq_type]

                if (slot_info.get(constants.ITEM_NAME_TYPE_METAL) is not None
                    and roll_percentage(20)): # 20% of light armor is metal
                    item_type = constants.ITEM_NAME_TYPE_METAL
                names = slot_info[item_type]

            descriptors = ITEM_DESCRIPTORS[item_type].get(main_stat)
            if descriptors is not None:
                adj = random.choice(descriptors)
                name = random.choice(names)

                if name[0] == ' ':
                    return 'a set of {adj}{name}'.format(adj=adj.split()[-1],
                                                          name=name)
                elif eq_type in PAIR_ARMOR_TYPES:
                    group = random.choice(PAIR_NAMES)
                    split_adj = adj.split()[-1]
                    return 'a {group} of {adj} {name}'.format(group=group,
                                                              adj=split_adj,
                                                              name=name)
                else:
                    return '{adj} {name}'.format(adj=adj, name=name)



        if armor_class == constants.ARMOR_CLASS_LIGHT:
            if main_stat in (constants.ATTR_INT,
                             constants.ATTR_CON,
                             constants.ATTR_DEX):
                return SPECIAL_SETS[armor_class][main_stat][eq_type]

        elif main_stat in (constants.ATTR_STR,
                           constants.ATTR_CON):
            return SPECIAL_SETS[armor_class][main_stat][eq_type]

        adj = MAGIC_ADJECTIVES[main_stat][armor_class]

    return basic_eq_unfmt_str.format(adj=adj)


def name_shield(level, armor_class, quality, stats):
    # Start with the normal name
    name = NORMAL_SHIELD_NAMES[armor_class]

    # If magic, see if the class / main stat combo yields a better name
    if quality != constants.ITEM_QUALITY_NORMAL:
        main_stat = item_utils.get_main_primary_stat(stats)

        if level > REVAMP_LEVEL_THRESHOLD:
            descriptors = ITEM_DESCRIPTORS[constants.ITEM_NAME_TYPE_METAL]
            adj = random.choice(descriptors.get(main_stat))
            name = random.choice(SHIELD_NAMES[armor_class])
            if adj is not None and name is not None:
                return '{adj} {name}'.format(adj=adj, name=name)

        special_name = MAGIC_SHIELD_NAMES[armor_class].get(main_stat)
        name = special_name if special_name else name

    return name


def name_weapon(level, eq_type, quality, stats):
    if quality == constants.ITEM_QUALITY_NORMAL:
        weapons = NORMAL_WEAPONS[eq_type]
    else:
        main_stat = item_utils.get_main_primary_stat(stats)

        if level > REVAMP_LEVEL_THRESHOLD:
            weapons = list(WEAPON_NAMES[eq_type].items())
            weap_type, weap_info = random.choice(weapons)
            classification = constants.WEAPON_CLASSIFICATIONS[weap_type]
            descriptors = ITEM_DESCRIPTORS[classification].get(main_stat)
            if descriptors is not None:
                adj = random.choice(descriptors)
                name = random.choice(weap_info['names'])
                weap_dict = weap_info['messages']
                weap_dict['weapon_type'] = weap_type
                # May need to refine keywords to filter out 'a', 'an', etc.
                weap_dict['keywords'] = adj.split() + name.split()

                if adj[0] == ' ':
                    weap_dict['name'] =  'a {name}{adj}'.format(adj=adj,
                                                                name=name)
                else:
                    weap_dict['name'] = '{adj} {name}'.format(adj=adj,
                                                              name=name)

                return weap_dict

        weapons = MAGIC_WEAPONS[eq_type][main_stat]
    return random.choice(weapons)