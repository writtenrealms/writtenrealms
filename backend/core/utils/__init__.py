import calendar
from contextlib import contextmanager
import datetime
import inspect
import json
import subprocess
import random
import re
import traceback

from jinja2 import Template
from jinja2.exceptions import TemplateSyntaxError, UndefinedError


def get_classes_in_module(module, base_cls):
    """
    Get all the class objects that are derived from a particular base class
    in a given module.
    """
    classes = []
    for cls in module.__dict__.values():
        try:
            if base_cls in inspect.getmro(cls) and cls != base_cls:
                classes.append(cls)
        except AttributeError:
            continue
    return classes


def CamelCase__to__camel_case(name):
    """
    From http://stackoverflow.com/questions/1175208/

    >>> CamelCase__to__camel_case('CamelCase')
    'camel_case'
    >>> CamelCase__to__camel_case('CamelCamelCase')
    'camel_camel_case'
    >>> CamelCase__to__camel_case('Camel2Camel2Case')
    'camel2_camel2_case'
    >>> CamelCase__to__camel_case('getHTTPResponseCode')
    'get_http_response_code'
    >>> CamelCase__to__camel_case('get2HTTPResponseCode')
    'get2_http_response_code'
    >>> CamelCase__to__camel_case('HTTPResponseCode')
    'http_response_code'
    >>> CamelCase__to__camel_case('HTTPResponseCodeXYZ')
    'http_response_code_xyz'
    """

    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

def capfirst(string):
    return string[0:1].upper() + string[1:]

# backwards compat
upper_first = capfirst

def pprint(payload):
    if type(payload) == list:
        payload = {'__list__': payload}

    if not isinstance(payload, dict):
        payload = json.loads(payload)

    def default(obj):
        try:
            return "<%s>" % obj.__str__()
        except AttributeError:
            return "Instance of %s" % obj.__class__.__name__

    print(json.dumps(payload, default=default, indent=4))

def shell(cmd):
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        shell=True).communicate()

def roll_percentage(chance):
    "Returns True or False based on whether the chance was triggered or not."
    try:
        chance = int(chance)
    except (ValueError, TypeError):
        chance = 0
    if chance > 100:
        chance = 100
    random_int = random.randrange(1, 101) # Return between 1 and 100
    return chance >= random_int


def roll_probability(probability):
    if probability > 1 or probability < 0:
        raise ValueError("Invalid probability: %s. Should be between 0 and 1." % probability)
    return random.random() <= probability


def parse_damage_string(dmg_string):

    # Case where the string is just digits, which indicates constant damage
    if dmg_string.isdigit():
        return {
            "num":  int(dmg_string),
            "size": 1,
            "operand": None,
            "argument": None
        }

    # Break down a damage string into its 4 components.
    exp = re.compile("""
        ^(?P<num>\d+)\w(?P<size>\d+) # AdX
        ((?P<op>[\+*/-])(?P<arg>\d+))?$ # +C
        """, re.VERBOSE)
    m = exp.match(dmg_string)
    if m is None:
        raise ValueError("Invalid roll descriptor: %s" % dmg_string)
    groupdict = m.groupdict()
    return {
        "num": int(groupdict['num']),
        "size": int(groupdict['size']),
        "operand": groupdict['op'],
        "argument": None if groupdict['arg'] is None else int(groupdict['arg'])
    }


def roll_die(desc=None, num=None, size=None, operand=None, argument=None):
    """
    Simulates dice roll. Can be invoked either by passing a description string
    like "2d6+10", or by passing the individual parameters. Operand can be
    +, -, * or /.
    """
    result = 0
    num = size = operand = argument = None

    if desc is not None:
        try:
            parsed = parse_damage_string(desc)
            num = parsed['num']
            size = parsed['size']
            operand = parsed['operand']
            argument = parsed['argument']
        except ValueError:
            pass

    # Do the AdX part
    if num is not None and size is not None:
        if num >= 0 and size >= 0:

            # Anti abuse maximums
            if size > 100:
                size = 100
            if num > 100:
                num = 100

            for i in range(0, num):
                result += random.randrange(1, size+1)

    # Do the +C part
    if operand is not None and argument is not None:
        if operand == '+':
            result += argument
        elif operand == '-':
            result -= argument
        elif operand == '*':
            result *= argument
        elif operand == '/':
            result /= argument

    return result


def average_damage(dmg_string):
    "Calculate the average expected damage of a damage string."
    parsed = parse_damage_string(dmg_string)
    result = parsed['num'] * (parsed['size'] + 1) / 2
    if parsed['operand'] == '+':
        result += parsed['argument']
    elif parsed['operand'] == '-':
        result -= parsed['argument']
    elif parsed['operand'] == '*':
        result *= parsed['argument']
    elif parsed['operand'] == '/':
        result /= parsed['argument']
    return result


def round_float(number, decimals):
    "returns a new float rounded out to the specified decimal places"
    str_format = "%%.0%if" % decimals
    return float(str_format % number)


@contextmanager
def warnexception():
    try:
        yield
    except Exception:
        traceback.print_exc()

EPOCH = datetime.datetime.utcfromtimestamp(0)
def ss_epoch(dt=None):
    "Return number of seconds since epoch"
    if dt is None:
        dt = datetime.datetime.utcnow()
    return (dt - EPOCH).total_seconds()

def expiration_ts(seconds):
    "Returns expiration timestamp in seconds from now to epoch"
    return ss_epoch(
        datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds))

def distinct_list(lst):
    """
    Remove duplicates from a list while preserving original order.

    :param lst: list to remove duplicates from
    :type lst: list
    :return: list with duplicates removed
    :rtype: list
    """

    dlst = []
    for i in lst:
        if i not in dlst:
            dlst.append(i)
    return dlst

def unix_ts(ts=None):
    "returns current unix timestamp, should be pretty equivalent to ss_epoch"
    if not ts:
        ts = datetime.datetime.utcnow()
    return calendar.timegm(ts.utctimetuple())

def split_cmd(cmd):
    """
    Do a smart split of a cmd based on ; unless in quotes.

    Base on responses here:
    https://stackoverflow.com/questions/2785755/how-to-split-but-ignore-separators-in-quoted-strings-in-python
    """
    return re.split(''';(?=(?:[^'"]|'[^']*'|"[^"]*")*$)''', cmd)

    # Alternate implementation that seems to choke when there's standalone
    # apostrophes, for example "This'll do" would actually do a split.
    # Hence why we're using the one above.
    PATTERN = re.compile(r'''((?:[^;"']|"[^"]*"|'[^']*')+)''')
    return PATTERN.split(cmd)[1::2]


def roll_variance(value, range, strictly_positive=False):
    """
    Vary a value by a % range
    """

    if strictly_positive:
        return round(
            value * (1 + random.randrange(0, range + 1) / 100))

    return round(
        value * (1 + random.randrange(-range, range + 1) / 100))


def format_actor_msg(msg, actor=None):
    """
    Careful accessing 'actor' attributes here, as this can be called
    by the game engine but also by the forge (for example for the quest
    log).
    """
    if not msg or not actor: return msg

    if actor.__class__.__name__ == 'Room': return msg

    if actor.__class__.__name__ == 'Player':
        name = actor.name
    else:
        name = actor.keywords.split(' ')[0]

    message_data = {
        'actor_key': actor.key,
        'actor': name,
        'actor_marks': {},
        'facts': {},
        'actor_data': {},
        'actor_subject_pronoun': 'they',
        'actor_object_pronoun': 'them',
        'actor_possessive_adjective': 'their',
        'actor_possessive_pronoun': 'theirs',
        'actor_reflexive_pronoun': 'themselves',
    }

    # Marks & Facts
    if actor.__class__.__module__ == 'spawns.models':
        actor_marks = dict(actor.marks.values_list('name', 'value'))
        facts = json.loads(actor.world.facts or '{}')
    else:
        actor_marks = actor.marks or {}
        facts = actor.world.facts or {}
    message_data['actor_marks'] = actor_marks
    message_data['facts'] = facts

    # Actor Data
    for column in ['level', 'experience']:
        message_data['actor_data'][column] = getattr(actor, column)

    # Pronouns

    (message_data['actor_subject_pronoun'],
     message_data['actor_object_pronoun'],
     message_data['actor_possessive_adjective'],
     message_data['actor_possessive_pronoun'],
     message_data['actor_reflexive_pronoun']) = actor.pronouns

    try:
        raw_template = Template(msg, extensions=['jinja2.ext.loopcontrols'])
        parsed_msg = raw_template.render(message_data)
    except (TemplateSyntaxError, UndefinedError):
        print("Invalid actor %s message template: %s" % (actor.key, msg))
        traceback.print_exc()
        return msg

    return parsed_msg


def seconds_since(ts):
    "Returns the number of seconds between now and the provided timestamp."
    return (datetime.datetime.now() - ts).total_seconds()

def has_number(inputString):
    return any(char.isdigit() for char in inputString)

def is_ascii(string):
    for char in string:
        if ord(char) > 127:
            return False
    return True
