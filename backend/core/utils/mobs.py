import math

from core.computations import compute_stats

from config import game_settings as config

def suggest_stats(level=1, archetype='warrior', adjust=True, is_elite=False):
        stats = compute_stats(level, archetype, boost_mob=True)
        stats.pop('health_base', None)
        stats.pop('stamina_base', None)
        if 'mana_base' in stats: stats.pop('mana_base', None)

        # For mobs, we don't want the 4 base stats so we remove them here
        stats.pop('strength', None)
        stats.pop('dexterity', None)
        stats.pop('intelligence', None)
        stats.pop('constitution', None)

        # ==== Stats Reduction ====
        REDUCED_STATS = ['health_max']

        # Reduction coefficient for low level mobs. Doubles each level.
        # If C is 10, a level 1 mob will have 10% health,
        #             a level 2 mob will have 20% health, etc.
        # If C is 100, level 1 will have 100% health.
        # If C is 0, level 1 will have 0 health
        C = 15
        # Determine at which level to stop given the coefficient
        L = math.floor(100 / C)
        if adjust and level <= L:
            for stat in REDUCED_STATS:
                stats[stat] = math.ceil(stats[stat] * level * C / 100)

        # Add suggested exp
        stats['exp_worth'] = config.MOB_EXP.get(level)

        if is_elite:
            for stat, value in stats.items():
                boost = config.ELITE_BOOST.get(stat)
                if boost:
                    stats[stat] = value * boost
                else:
                    stats[stat] = value * config.ELITE_BOOST_DEFAULT

        return stats

mob_stat_keys = [
        'armor',
        'crit',
        'dodge',
        'resilience',
        'health_max',
        'mana_max',
        'attack_power',
        'spell_power',
        'mana_regen',
        'health_regen']

def stats_from_dictionary(mob_dict):
    # Generates base_stats to allow mobs to return to their normal state if
    # compute_stats is called
    try:
        return { key: mob_dict[key] for key in mob_stat_keys }
    except:
        return {}
