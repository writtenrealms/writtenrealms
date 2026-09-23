"""Bounded, ordered initial-instance routing in the shared condition DSL."""
from copy import deepcopy
import re

from core.condition_dsl import ConditionContext, evaluate_condition, validate_condition_payload


MAX_INSTANCE_ROUTES = 32
CREATION_PATHS = {'player.core_faction', 'player.archetype', 'player.gender', 'player.level'}


def normalize_instance_routes(value, *, world=None):
    field = 'player_creation.instance_routes'
    if not isinstance(value, list) or len(value) > MAX_INSTANCE_ROUTES:
        raise ValueError(f'{field} must be a list of at most {MAX_INSTANCE_ROUTES} routes.')
    if world is not None and (world.context_id or world.instance_of_id or not world.is_multiplayer):
        if value:
            raise ValueError(f'{field} is only configurable on a multiplayer base world.')
    faction_codes = set()

    def validate(condition, name):
        if isinstance(condition, bool):
            return
        if not isinstance(condition, dict) or len(condition) != 1:
            raise ValueError(f'{name} requires one condition operator per mapping.')
        operator, operands = next(iter(condition.items()))
        if operator in {'all', 'any'}:
            if not isinstance(operands, list) or not operands:
                raise ValueError(f'{name}.{operator} must be a nonempty list.')
            for child in operands:
                validate(child, name)
        elif operator == 'not':
            validate(operands, name)
        elif operator == 'always' and isinstance(operands, bool):
            return
        elif operator in {'eq', 'ne', 'in', 'gte', 'lte'}:
            if not isinstance(operands, list) or len(operands) != 2:
                raise ValueError(f'{name}.{operator} requires two operands.')
            path, literal = operands
            if not isinstance(path, str) or path not in CREATION_PATHS:
                raise ValueError(f'{name} must compare one of: {", ".join(sorted(CREATION_PATHS))}.')
            if operator in {'gte', 'lte'} and path != 'player.level':
                raise ValueError(f'{name}.{operator} is only supported for player.level.')
            if operator == 'in':
                if not isinstance(literal, list) or not 1 <= len(literal) <= 64:
                    raise ValueError(f'{name}.in requires a list of 1–64 literal values.')
                literals = literal
            else:
                literals = [literal]
            for item in literals:
                if path == 'player.level':
                    if type(item) is not int:
                        raise ValueError(f'{name} must compare player.level with integers.')
                elif item is not None and (
                    not isinstance(item, str) or not item or len(item) > 120
                    or '{' in item or '}' in item
                ):
                    raise ValueError(f'{name} requires literal text or null.')
                if path == 'player.core_faction' and item is not None:
                    faction_codes.add(item)
        else:
            raise ValueError(f'{name} uses an unsupported character-creation condition operator.')

    for index, route in enumerate(value):
        name = f'{field}[{index}]'
        if not isinstance(route, dict) or set(route) != {'when', 'instance'}:
            raise ValueError(f'{name} requires exactly when and instance.')
        slug = route['instance']
        if not isinstance(slug, str) or len(slug) > 120 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug):
            raise ValueError(f'{name}.instance must be a stable instance slug.')
        validate_condition_payload(route['when'], field_name=f'{name}.when')
        validate(route['when'], f'{name}.when')

    if world is not None and value:
        from core.factions import validate_core_faction_codes
        from worlds.models import World

        if faction_codes:
            validate_core_faction_codes(world=world, codes=list(faction_codes), field_name=field)
        templates = {template.instance_slug: template for template in World.objects.filter(
            instance_of=world, context__isnull=True,
            instance_slug__in={route['instance'] for route in value},
        ).select_related('config', 'config__starting_room')}
        for route in value:
            validate_initial_instance_destination(templates.get(route['instance']), slug=route['instance'])
    return deepcopy(value)


def validate_initial_instance_destination(template, *, slug):
    from config import constants

    if template is None:
        raise ValueError(f"Initial instance '{slug}' is not an instance template in this base world.")
    if template.lifecycle == constants.WORLD_STATE_ARCHIVED:
        raise ValueError(f"Initial instance '{slug}' is archived.")
    config = template.config
    if not config or not config.instance_single_player:
        raise ValueError(f"Initial instance '{slug}' requires a single-player instance.")
    if not config.starting_room or config.starting_room.world_id != template.pk:
        raise ValueError(f"Initial instance '{slug}' needs a starting room inside its template.")


def _snapshot_condition(condition):
    """Evaluate the same DSL against plain data, with no ORM traversal."""
    if isinstance(condition, bool):
        return condition
    operator, operands = next(iter(condition.items()))
    if operator in {'all', 'any'}:
        return {operator: [_snapshot_condition(child) for child in operands]}
    if operator == 'not':
        return {operator: _snapshot_condition(operands)}
    if operator == 'always':
        return condition
    return {operator: [f'event.{operands[0]}', operands[1]]}


def select_initial_instance_slug(player, *, routes):
    if not routes:
        return None
    routes = normalize_instance_routes(routes)
    faction = player.core_faction if player.core_faction_id else None
    context = ConditionContext(event_data={'player': {
        'core_faction': faction.code if faction else None,
        'archetype': player.archetype,
        'gender': player.gender,
        'level': player.level,
    }})
    for route in routes:
        if evaluate_condition(_snapshot_condition(route['when']), context=context):
            return route['instance']
    return None
