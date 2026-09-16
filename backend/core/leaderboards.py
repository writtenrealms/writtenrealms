"""Authored lobby panels. Ranking rules belong to the supported panel types."""
from copy import deepcopy


def default_leaderboards():
    return [{'type': 'glory_experience'}]


def normalize_leaderboards(value, *, world):
    if world is None or world.instance_of_id or world.context_id:
        raise ValueError('leaderboards must be configured on an authored base world.')
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError('spec.leaderboards must be a list of at most 8 panels; use [] to hide them.')
    types = {
        'glory_experience': set(),
        'dueling': {'minimum_matches'},
        'instance_clear_time': {'instance', 'timing'},
    }
    for index, panel in enumerate(value):
        field = f'spec.leaderboards[{index}]'
        if not isinstance(panel, dict) or not isinstance(panel.get('type'), str) or panel['type'] not in types:
            raise ValueError(f'{field}.type must be instance_clear_time, dueling, or glory_experience.')
        if any(not isinstance(key, str) for key in panel):
            raise ValueError(f'{field} settings must have text keys.')
        unknown = set(panel) - {'type', 'title', 'limit'} - types[panel['type']]
        if unknown:
            raise ValueError(f'{field} has unsupported settings: {", ".join(sorted(unknown))}.')
        if 'title' in panel and (not isinstance(panel['title'], str) or not panel['title'].strip() or len(panel['title']) > 80):
            raise ValueError(f'{field}.title must contain 1–80 characters.')
        for name, maximum in [('limit', 50), ('minimum_matches', 10000)]:
            if name in panel and (type(panel[name]) is not int or not 1 <= panel[name] <= maximum):
                raise ValueError(f'{field}.{name} must be an integer from 1 to {maximum}.')
        if panel['type'] == 'instance_clear_time':
            if not isinstance(panel.get('instance'), str) or not panel['instance']:
                raise ValueError(f'{field}.instance must be an instance slug from this world.')
            if panel.get('timing', 'current') not in ('current', 'time_control', 'continuous'):
                raise ValueError(f'{field}.timing must be current, time_control, or continuous.')
    from worlds.models import World
    slugs = {p['instance'] for p in value if p['type'] == 'instance_clear_time'}
    templates = {w.instance_slug: w for w in World.objects.filter(
        instance_of=world, context_id__isnull=True, instance_slug__in=slugs,
    ).select_related('config')}
    for index, panel in enumerate(value):
        if panel['type'] != 'instance_clear_time':
            continue
        template = templates.get(panel['instance'])
        if template is None:
            raise ValueError(f"spec.leaderboards[{index}].instance: '{panel['instance']}' is not an instance in this world.")
        if not template.config.instance_goal:
            raise ValueError(f"spec.leaderboards[{index}].instance: '{panel['instance']}' needs completion criteria first.")
    return deepcopy(value)
