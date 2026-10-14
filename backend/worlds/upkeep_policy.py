"""Portable upkeep policy; runtime deadlines snapshot these values once."""
from copy import deepcopy

from rest_framework.exceptions import ValidationError


MAX_LIFETIME_SECONDS = 365 * 24 * 60 * 60
DEFAULT_UPKEEP = {
    'corpses': {
        'npc': {'unclaimed': {'mode': 'fixed', 'seconds': 600},
                'empty': {'mode': 'fixed', 'seconds': 60}},
        'player': {'unclaimed': {'mode': 'none'},
                   'empty': {'mode': 'fixed', 'seconds': 600}},
    },
    'ground_items': {'abandoned': {'mode': 'fixed', 'seconds': 3600}},
}


def normalize_timer(value, *, field='upkeep', inherit=False, player_unclaimed=False):
    if not isinstance(value, dict):
        raise ValidationError(f'{field} must be a mapping.')
    mode = value.get('mode')
    modes = {'none', 'fixed'} | ({'inherit_world'} if inherit else set())
    if not isinstance(mode, str) or mode not in modes or set(value) - {'mode', 'seconds'}:
        raise ValidationError(f'{field} requires mode {", ".join(sorted(modes))}.')
    if player_unclaimed and mode != 'none':
        raise ValidationError('Player corpses containing equipment do not support timed expiration yet.')
    if mode != 'fixed':
        if 'seconds' in value:
            raise ValidationError(f'{field}.seconds is only valid with mode fixed.')
        return {'mode': mode}
    seconds = value.get('seconds')
    if type(seconds) is not int or not 1 <= seconds <= MAX_LIFETIME_SECONDS:
        raise ValidationError(f'{field}.seconds must be an integer from 1 to {MAX_LIFETIME_SECONDS}.')
    return {'mode': mode, 'seconds': seconds}


def normalize_upkeep(value, *, existing=None, instance=False):
    """Deep partial update; an instance stores only its explicit overrides."""
    if not isinstance(value, dict):
        raise ValidationError('spec.upkeep must be a mapping.')
    if instance and value == {'mode': 'inherit_world'}:
        return {}
    result = deepcopy(existing if existing is not None else ({} if instance else DEFAULT_UPKEEP))

    def merge(patch, schema, target, path):
        if not isinstance(patch, dict) or set(patch) - set(schema):
            raise ValidationError(f'{path} has unsupported fields or is not a mapping.')
        for key, child in patch.items():
            at = f'{path}.{key}'
            if 'mode' in schema[key]:
                target[key] = normalize_timer(child, field=at,
                    player_unclaimed=at.endswith('.player.unclaimed'))
            else:
                merge(child, schema[key], target.setdefault(key, {}), at)
    merge(value, DEFAULT_UPKEEP, result, 'spec.upkeep')
    return result


def authored_upkeep(world):
    config = world.config
    if world.instance_of_id:
        return deepcopy(config.upkeep) or {'mode': 'inherit_world'}
    return normalize_upkeep(config.upkeep)


def effective_upkeep(world):
    """Runtime instances retain their creation snapshot; base policy is live."""
    if world.context_id:
        if world.upkeep_snapshot:
            return deepcopy(world.upkeep_snapshot)
        world = world.context
    if world.instance_of_id:
        base = effective_upkeep(world.instance_of)
        return normalize_upkeep(world.config.upkeep, existing=base)
    return normalize_upkeep(world.config.upkeep)


def timer_seconds(timer):
    return timer['seconds'] if timer['mode'] == 'fixed' else None


def normalize_storage(value):
    if not isinstance(value, dict) or set(value) - {'contents'}:
        raise ValidationError('entry.upkeep accepts only contents: cleanup or preserve.')
    contents = value.get('contents', 'cleanup')
    if not isinstance(contents, str) or contents not in {'cleanup', 'preserve'}:
        raise ValidationError('entry.upkeep.contents must be cleanup or preserve.')
    return {'contents': contents}
