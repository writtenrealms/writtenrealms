import json
import jsonschema
import re

from core.condition_dsl import (
    ConditionContext,
    evaluate_condition as evaluate_structured_condition,
    structured_condition_payload,
)
from core.scoped_state import (
    STATE_SCOPE_CHARACTER,
    STATE_SCOPE_ROOM,
    STATE_SCOPE_WORLD,
    STATE_SCOPE_ZONE,
    get_state_snapshot,
)
from worlds.models import World

CONDITIONS = [
    {
        'name': 'archetype',
        'args': [str]
    },
    {
        'name': 'core_faction',
        'args': [str]
    },
    {
        'name': 'fact_check',
        'args': [str, str]
    },
    {
        'name': 'fact_above',
        'args': [str, int]
    },
    {
        'name': 'gender',
        'args': [str]
    },
    {
        'name': 'has_shield',
        'args': []
    },
    {
        'name': 'has_weapon',
        'args': []
    },
    {
        'name': 'health',
        'args': [int]
    },
    { # Deprecated
        'name': 'health_below',
        'args': [int]
    },
    {
        'name': 'in_combat',
        'args': [],
    },
    {
        'name': 'is_following',
        'args': [],
    },
    {
        'name': 'is_mob',
        'args': []
    },
    {
        'name': 'item_in_eq',
        'args': [int],
    },
    {
        'name': 'item_in_inv',
        'args': [int]
    },
    {
        'name': 'item_in_room',
        'args': [int]
    },
    { # Deprecated
        'name': 'level_above',
        'args': [int]
    },
    { # Deprecated
        'name': 'level_below',
        'args': [int]
    },
    {
        'name': 'level',
        'args': [int]
    },
    {
        'name': 'name',
        'args': [str],
    },
    {
        'name': 'marked',
        'args': [str, str]
    },
    {
        'name': 'mark_above',
        'args': [str, int]
    },
    {
        'name': 'mob_in_room',
        'args': [int]
    },
    {
        'name': 'player_in_room',
        'args': [],
    },
    {
        'name': 'quest_complete',
        'args': [int]
    },
    { # Deprecated
        'name': 'standing_above',
        'args': [str, int]
    },
    {
        'name': 'standing',
        'args': [str, int]
    },
    {
        'name': 'wields_weapon_type',
        'args': [str]
    },
]


EQUIPMENT_SLOTS = (
    'weapon',
    'offhand',
    'head',
    'shoulders',
    'body',
    'arms',
    'hands',
    'waist',
    'legs',
    'feet',
    'accessory',
)


def _json_to_dict(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _serialize_item(item):
    if not item:
        return {}
    return {
        'key': getattr(item, 'key', ''),
        'name': getattr(item, 'name', ''),
        'keywords': getattr(item, 'keywords', ''),
        'definition_id': str(getattr(item, 'definition_id', '') or ''),
        'equipment_type': getattr(item, 'equipment_type', '') or '',
        'weapon_type': getattr(item, 'weapon_type', '') or '',
    }


def _serialize_equipment(actor):
    data = {}
    equipment = getattr(actor, 'equipment', None)
    if not equipment:
        return data
    for slot in EQUIPMENT_SLOTS:
        item = getattr(equipment, slot, None)
        if item:
            data[slot] = _serialize_item(item)
    return data


def _serialize_inventory(actor):
    inventory = []
    inventory_manager = getattr(actor, 'inventory', None)
    if inventory_manager is None:
        return inventory

    for item in inventory_manager.filter(is_pending_deletion=False):
        inventory.append(_serialize_item(item))
    return inventory


def _serialize_marks(actor):
    return get_state_snapshot(STATE_SCOPE_CHARACTER, actor)


def _serialize_target(actor):
    target = getattr(actor, 'target', None)
    if not target:
        return {}
    target_keywords = getattr(target, 'keywords', '') or getattr(target, 'name', '')
    return {
        'key': getattr(target, 'key', ''),
        'keywords': target_keywords,
    }


def _build_actor_data(actor):
    health = int(getattr(actor, 'health', 0) or 0)
    health_max = int(getattr(actor, 'health_max', health) or health or 1)
    return {
        'key': getattr(actor, 'key', '') or '',
        'name': getattr(actor, 'name', '') or '',
        'archetype': getattr(actor, 'archetype', '') or '',
        'gender': getattr(actor, 'gender', '') or '',
        'level': int(getattr(actor, 'level', 0) or 0),
        'health': health,
        'health_max': health_max,
        'state': getattr(actor, 'state', '') or '',
        'following': bool(getattr(actor, 'following', None)),
        'factions': getattr(actor, 'factions', {}) or {},
        'marks': _serialize_marks(actor),
        'equipment': _serialize_equipment(actor),
        'inventory': _serialize_inventory(actor),
        'target': _serialize_target(actor),
    }


def _build_room_data(room):
    data = {
        'inventory': [],
        'chars': [],
        'state': {},
        'zone_state': {},
    }
    if not room:
        return data

    data['state'] = get_state_snapshot(STATE_SCOPE_ROOM, room)
    data['zone_state'] = get_state_snapshot(STATE_SCOPE_ZONE, getattr(room, 'zone', None))

    room_items = room.inventory.filter(is_pending_deletion=False)
    for item in room_items:
        data['inventory'].append(_serialize_item(item))

    room_players = room.players.filter(in_game=True)
    for player in room_players:
        data['chars'].append({
            'key': player.key,
            'definition_id': None,
        })

    room_mobs = room.mobs.filter(is_pending_deletion=False)
    for mob in room_mobs:
        data['chars'].append({
            'key': mob.key,
            'definition_id': mob.definition_id,
        })

    return data


def _build_world_data(world):
    return {
        'facts': get_state_snapshot(STATE_SCOPE_WORLD, world),
    }


def evaluate_conditions(
    actor,
    text,
    *,
    room=None,
    zone=None,
    world=None,
    event_data=None,
):
    """
    Top level call for evaluating conditions. Multiple condition blocks can be chained
    with 'and', 'or', 'not', or parentheses.

    The vast majority of the time, this is called from the game side, where 'actor' is a
    Player game model. Spawn-world conditions can also pass the spawned world
    as the actor.

    returns {
        'result': True|False,
        'detail': 'Reason for failure'
    }
    """

    structured_condition = structured_condition_payload(text)

    # We fetch this data up front so that each condition has all of the data it needs.
    actor_data = {}
    room_data = {'inventory': [], 'chars': []}
    world_data = {'facts': {}}

    try:
        from spawns.models import Mob, Player
    except Exception:
        Mob = None
        Player = None

    if Player and Mob and (isinstance(actor, Player) or isinstance(actor, Mob)):
        actor_data = _build_actor_data(actor)
        condition_room = room if room is not None else getattr(actor, 'room', None)
        condition_world = world if world is not None else getattr(actor, 'world', None)
        room_data = _build_room_data(condition_room)
        world_data = _build_world_data(condition_world)
    elif isinstance(actor, World):
        condition_world = world if world is not None else actor
        world_data = _build_world_data(condition_world)
    else:
        # We can't test for an API world object as the legacy game module doesn't
        # have access to the API. So we just make sure that the model's name is
        # correct and then make the assumption it's an API world.
        if actor.__class__.__name__ == 'World':
            condition_world = world if world is not None else actor
            world_data = _build_world_data(condition_world)

    if structured_condition is not None:
        condition_room = room if room is not None else getattr(actor, 'room', None)
        condition_zone = (
            zone
            if zone is not None
            else getattr(condition_room, 'zone', None)
        )
        condition_world = (
            world
            if world is not None
            else getattr(actor, 'world', actor if isinstance(actor, World) else None)
        )
        return {
            'result': evaluate_structured_condition(
                condition=structured_condition,
                context=ConditionContext(
                    actor=actor,
                    actor_data=actor_data,
                    room_data=room_data,
                    world_data=world_data,
                    room=condition_room,
                    zone=condition_zone,
                    world=condition_world,
                    event_data=event_data if isinstance(event_data, dict) else {},
                ),
            ),
            'detail': '',
        }

    # world_data schema validation
    try:
        world_data_schema = {
            'type': 'object',
            'properties': {
                'facts': {
                    'type': ['object', 'null']
                }
            }
        }
        jsonschema.validate(instance=world_data, schema=world_data_schema)
    except jsonschema.exceptions.ValidationError as e:
        raise Exception(f"Invalid world data: {e}")

    evaluated_tokens = []

    # We want to know whether or not we've encountered a single 'or' because
    # if we haven't, then all conditions are 'and's, which means we can return
    # the failure reason as the first encountered one. If there is at least
    # one 'or' or 'not' then we don't bother, because it would be too
    # complicated to extract the reason from the failed condition.
    complex_expression = False
    first_error_reason = None

    text_segments = break_text(text)

    for segment in text_segments:
        if segment in ('or', 'not'):
            complex_expression = True

        if segment not in BREAK_TOKENS:

            try:
                evaluated_condition = evaluate_condition(
                    world_data=world_data,
                    actor_data=actor_data,
                    room_data=room_data,
                    text=segment)
            except:
                evaluated_condition = {
                    'result': False,
                    'detail': "Invalid condition '%s'" % segment
                }

            if not evaluated_condition['result']:
                first_error_reason = evaluated_condition['detail']
            evaluated_tokens.append(
                str(evaluated_condition['result']))
        else:
            evaluated_tokens.append(segment)


    conditions = ' '.join(evaluated_tokens)

    try:
        result = eval(conditions, {"__builtins__": None}, {})
    except:
        result = False
    detail = ''
    if not complex_expression and first_error_reason:
        detail = first_error_reason

    return {
        'result': result,
        'detail': detail,
    }


def return_true(detail=None):
    return {
        'result': True,
        'detail': detail or ''
    }

def return_false(detail=None):
    return {
        'result': False,
        'detail': detail or ''
    }

BREAK_TOKENS = ['(', ')', 'and', 'or', 'not']

def break_text(text):
    """
    Break text into a list of tokens, for example (a or (b or c)) would become
    ['(', 'a', 'or', '(', 'b', 'or', 'c', ')', ')']
    """
    text = text.lower()
    text = text.replace('(', ' ( ')
    text = text.replace(')', ' ) ')

    # This will genearate a list of tokens broken down by space.
    tokens = text.split()
    # But we need to re-combine tokens that should not be used to break up
    # the string. For example, level_above 1 and level_above 2 should return
    # ['level_above 1', 'and', 'level_above 2'], rather than
    # ['level_above', '1', 'and', ...

    # The final tokens once this process is done
    final_tokens = []
    # Non-break tokens in the process of being recombined
    recombined_tokens = []

    for token in tokens:
        if token in BREAK_TOKENS:
            if recombined_tokens:
                final_tokens.append(' '.join(recombined_tokens))
            recombined_tokens = []
            final_tokens.append(token)
        else:
            recombined_tokens.append(token)

    if recombined_tokens:
        final_tokens.append(' '.join(recombined_tokens))

    return final_tokens


def evaluate_condition(world_data, actor_data, room_data, text):
    """
    Evaluate a single condition, meaning it should have no and, or, or
    parentheses.
    """

    tokens = [ t.lower() for t in re.split(r'\s+', text) ]
    condition_name = tokens[0]
    args = tokens[1:]

    # Find the condition name
    try:
        condition_spec = [
            spec for spec in CONDITIONS
            if spec['name'] == condition_name
        ][0]
    except IndexError:
        return return_false('Invalid condition name %s' % condition_name)

    if len(args) < len(condition_spec['args']):
        error = (
            "Not enough arguments for {name}. "
            "Need {need}, passed {passed}").format(
                name=condition_name,
                need=len(condition_spec['args']),
                passed=len(args))
        return return_false(error)

    # Archetype
    if (condition_name == 'archetype'):
        archetype = str(tokens[1])
        if actor_data['archetype'] == archetype:
            return return_true()
        return return_false("You are not a %s." % archetype)

    # Core faction
    if (condition_name == 'core_faction'):
        core_faction = str(tokens[1])
        actor_factions = actor_data.get('factions', {}) or {}
        if actor_factions.get('core') == core_faction:
            return return_true()
        return return_false("You are not of this core faction.")

    # Fact check
    if (condition_name == 'fact_check'):
        fact = tokens[1]
        value = tokens[2]
        facts = world_data.get('facts') or {}
        if fact not in facts:
            return return_false("Fact is not set.")
        elif str(value) == str(facts[fact]):
            return return_true()
        return return_false("Fact differs.")

    # Fact Above
    if (condition_name == 'fact_above'):
        fact = tokens[1]

        try:
            value = float(tokens[2])
        except (ValueError, TypeError):
            return return_false("Value is not a number.")

        facts = world_data.get('facts') or {}
        if fact not in facts:
            return return_false("Fact is not set.")

        try:
            if float(facts[fact]) > value:
                return return_true()
        except (ValueError, TypeError):
            return return_false("Fact is not a number.")

        return return_false("Fact is not above %s." % value)

    # Gender
    if (condition_name == 'gender'):
        gender = str(tokens[1])
        if actor_data['gender'] == gender:
            return return_true()
        return return_false("You are %s." % actor_data['gender'])

    # Has Shield
    if (condition_name == 'has_shield'):
        eq = actor_data.get('equipment') or {}
        offhand = eq.get('offhand') or {}
        offhand_type = offhand.get('equipment_type', '')
        if offhand_type == 'shield':
            return return_true()
        return return_false("No shield equipped.")

    # Has Weapon
    if (condition_name == 'has_weapon'):
        if (actor_data.get('equipment') or {}).get('weapon'):
            return return_true()
        return return_false("No weapon equipped.")

    # Health
    if (condition_name == 'health'):
        threshold = float(tokens[1])
        health = actor_data['health']
        health_max = actor_data['health_max'] or 1
        perc = health / health_max * 100
        if perc >= threshold:
            return return_true()
        return return_false("Not enough health.")

    # Health below (DEPRECATED)
    if (condition_name == 'health_below'):
        threshold = float(tokens[1])
        health = actor_data['health']
        health_max = actor_data['health_max'] or 1
        perc = health / health_max * 100
        if perc < threshold:
            return return_true()
        return return_false("Health is too high.")

    # In Combat
    if (condition_name == 'in_combat'):
        if actor_data['state'] == 'combat':

            # See if a target argument was also passed in
            try:
                target = str(tokens[1])
            except IndexError:
                return return_true()

            target = target.lower()
            actor_target = actor_data.get('target') or {}
            if actor_target:
                target_keywords = (actor_target.get('keywords') or '').lower()
                if target in target_keywords.split():
                    return return_true()
                return return_false("Not in combat against target.")

        return return_false("Not in combat.")

    # Is Following
    if (condition_name == 'is_following'):
        print('in condition')
        if actor_data.get('following'):
            return return_true()
        return return_false("Not following anyone.")

    # Is Mob
    if (condition_name == 'is_mob'):
        if actor_data['key'].startswith('mob'):
            return return_true()
        return return_false("You are not a mob.")

    # Equipped
    if (condition_name == 'item_in_eq'):
        definition_id = str(tokens[1])
        for item_data in actor_data['equipment'].values() or []:
            if item_data and item_data['definition_id'] == definition_id:
                return return_true()
        return return_false("Item not equipped.")

    # Item in room
    if (condition_name == 'item_in_room'):
        definition_id = int(tokens[1])
        try:
            desired_item_count = int(tokens[2])
        except IndexError:
            desired_item_count = 1
        items_found = 0
        for item_data in room_data['inventory']:
            if (item_data.get('definition_id', 0)
                and int(item_data.get('definition_id', 0)) == definition_id):
                items_found += 1
        if items_found >= desired_item_count:
            return return_true()
        return return_false("Required item is not in the room.")

    # Item in inv
    if (condition_name == 'item_in_inv'):
        definition_id = int(tokens[1])
        try:
            desired_item_count = int(tokens[2])
        except IndexError:
            desired_item_count = 1
        items_found = 0
        for item_data in actor_data['inventory']:
            if (item_data.get('definition_id', 0)
                and int(item_data.get('definition_id', 0) or 0) == definition_id):
                items_found += 1
        if items_found >= desired_item_count:
            return return_true()
        return return_false("Items not in inventory.")

    # Level
    if (condition_name == 'level'):
        if actor_data['level'] >= int(tokens[1]):
            return return_true()
        return return_false("You are not level %s." % int(tokens[1]))

    # Level above (DEPRECATED)
    if (condition_name == 'level_above'):
        if actor_data['level'] > int(tokens[1]):
            return return_true()
        return return_false("You are not above level %s." % int(tokens[1]))

    # Level below (DEPRECATED)
    if (condition_name == 'level_below'):
        if actor_data['level'] < int(tokens[1]):
            return return_true()
        return return_false("You are not below level %s." % int(tokens[1]))

    # Mark check
    if (condition_name == 'marked'):
        name = tokens[1]
        value = tokens[2]
        marks = actor_data.get('marks') or {}
        if name not in marks:
            return return_false("Player is not marked.")
        elif str(value) == str(marks[name]):
            return return_true()
        return return_false("Mark differs.")

    # Mark above
    if (condition_name == 'mark_above'):
        name = tokens[1]

        try:
            value = float(tokens[2])
        except (ValueError, TypeError):
            return return_false("Value is not a number.")

        marks = actor_data.get('marks') or {}
        if name not in marks:
            return return_false("Player is not marked.")

        try:
            if float(marks[name]) > value:
                return return_true()
        except (ValueError, TypeError):
            return return_false("Mark is not a number.")

        return return_false("Mark is not above %s." % value)

    # Mob in room
    if (condition_name == 'mob_in_room'):
        definition_id = int(tokens[1])
        try:
            desired_mob_count = int(tokens[2])
        except IndexError:
            desired_mob_count = 1
        mob_count = 0
        for char_data in room_data['chars']:
            if (int(char_data.get('definition_id', 0) or 0) == definition_id):
                mob_count += 1
        if mob_count >= desired_mob_count:
            return return_true()
        return return_false("Mob is not in room.")

    # Player in room
    if (condition_name == 'player_in_room'):
        for char_data in room_data['chars']:
            if char_data['key'].split('.')[0] == 'player':
                return return_true()
        return return_false("No player in room.")

    # Quest complete
    if (condition_name == 'quest_complete'):
        quest_id = int(tokens[1])
        return return_false("Quest complete not implemented.")
        """
        quest_key = namespace_key(world_data['id'], id=quest_id, type='quest')
        try:
            quest = db.fetch(quest_key)
            actor = db.fetch(actor_data['key'])
        except NotFound:
            return return_false("Required quest not completed.")
        if quest:
            player_quest = quest.get_combined_record(actor)
            if player_quest and player_quest.completion_ts:
                return return_true()
        return return_false("Required quest not completed.")
        """

    # Standing
    if (condition_name == 'standing'):
        faction = str(tokens[1])
        standing = int(tokens[2])
        actor_factions = actor_data.get('factions', {}) or {}
        if (actor_factions.get(faction) or 0) >= standing:
            return return_true()
        return return_false("Faction standing too low.")

    # Faction standing (DEPRECATED)
    if (condition_name == 'standing_above'):
        faction = str(tokens[1])
        standing = int(tokens[2])
        actor_factions = actor_data.get('factions', {}) or {}
        if (actor_factions.get(faction) or 0) > standing:
            return return_true()
        return return_false("Faction standing too low.")

    # Wields Weapon Type
    if (condition_name == 'wields_weapon_type'):
        weapon_type = tokens[1]
        weapon = actor_data['equipment'].get('weapon', {})
        if weapon and weapon.get('weapon_type') == weapon_type:
            return return_true()
        return return_false(f"You are not wielding a {weapon_type}.")

    # Name
    if (condition_name == 'name'):
        name = tokens[1]
        if actor_data['name'].lower() == name.lower():
            return return_true()
        return return_false("Name does not match.")

    return return_false("Condition not satisfied.")
