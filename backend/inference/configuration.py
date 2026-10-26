"""Small, versioned operator contract. All monetary arithmetic is exact."""
import json
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR


class Unavailable(Exception):
    """Safe reason code, suitable for metrics; never contains a prompt or key."""


DEFAULT_LIMITS = {
    'monthly_usd': '90', 'daily_usd': '3',
    'conversation_monthly_usd': '75', 'conversation_daily_usd': '2.50',
    'preparation_monthly_usd': '15', 'preparation_daily_usd': '0.50',
    'world_monthly_usd': '90', 'world_daily_usd': '3',
    'owner_daily_requests': 30, 'pending_requests': 16, 'concurrent_requests': 4,
}
RATE_KEYS = ('input', 'output', 'cached_input', 'cache_write')
PURPOSES = ('mob_conversation', 'mob_preparation', 'mob_evaluation')


def decimal(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > 1000000:
            raise ValueError()
        return number
    except (InvalidOperation, ValueError):
        raise Unavailable('invalid_configuration') from None


def micro(value):
    # A cap rounds down; reservations and charges round up.
    return int((decimal(value) * 1000000).to_integral_value(rounding=ROUND_FLOOR))


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Unavailable('invalid_configuration')
    return value


def validate_configuration(raw):
    if not isinstance(raw, dict) or set(raw) - {'provider', 'profiles', 'limits'}:
        raise Unavailable('invalid_configuration')
    if raw.get('provider') not in ('openai', 'anthropic'):
        raise Unavailable('invalid_provider')
    profiles = raw.get('profiles', {})
    if not isinstance(profiles, dict) or 'fast' not in profiles or set(profiles) - {'fast', 'deliberate'}:
        raise Unavailable('invalid_profiles')
    validated = {}
    for name, profile in profiles.items():
        required = {'model', 'price_revision', 'rates', 'max_input_tokens', 'max_output_tokens'}
        if not isinstance(profile, dict) or set(profile) != required:
            raise Unavailable('invalid_profile')
        for key in ('model', 'price_revision'):
            if not isinstance(profile[key], str) or not 1 <= len(profile[key]) <= 120:
                raise Unavailable('invalid_profile')
        rates = profile['rates']
        if not isinstance(rates, dict) or set(rates) != set(RATE_KEYS):
            raise Unavailable('missing_prices')
        rates = {key: str(decimal(value)) for key, value in rates.items()}
        if decimal(rates['input']) == 0 or decimal(rates['output']) == 0:
            raise Unavailable('missing_prices')
        validated[name] = {**profile, 'rates': rates,
            'max_input_tokens': integer(profile['max_input_tokens'], 4096, 32768),
            'max_output_tokens': integer(profile['max_output_tokens'], 16, 2048)}
    overrides = raw.get('limits', {})
    if not isinstance(overrides, dict) or set(overrides) - set(DEFAULT_LIMITS):
        raise Unavailable('invalid_limits')
    limits = {**DEFAULT_LIMITS, **overrides}
    for key in DEFAULT_LIMITS:
        if key.endswith('_usd'):
            limits[key] = str(decimal(limits[key]))
    for key, maximum in [('owner_daily_requests', 1000), ('pending_requests', 64), ('concurrent_requests', 16)]:
        limits[key] = integer(limits[key], 1, maximum)
    if limits['concurrent_requests'] > limits['pending_requests']:
        raise Unavailable('invalid_limits')
    for period in ('monthly', 'daily'):
        total = decimal(limits[f'{period}_usd'])
        if sum(decimal(limits[f'{purpose}_{period}_usd']) for purpose in ('conversation', 'preparation')) > total:
            raise Unavailable('allocations_exceed_budget')
        if decimal(limits[f'world_{period}_usd']) > total:
            raise Unavailable('world_exceeds_budget')
    return {'provider': raw['provider'], 'profiles': validated, 'limits': limits}


def maximum_cost(profile):
    rates = profile['rates']
    # USD / million tokens equals micro-USD / token. Reserve without cache discounts.
    input_rate = max(decimal(rates[key]) for key in ('input', 'cached_input', 'cache_write'))
    return int((profile['max_input_tokens'] * input_rate +
                profile['max_output_tokens'] * decimal(rates['output'])).to_integral_value(rounding=ROUND_CEILING))


def usage_cost(profile, usage):
    return int(sum(decimal(profile['rates'][key]) * usage[key] for key in RATE_KEYS)
               .to_integral_value(rounding=ROUND_CEILING))


def validate_payload(instructions, messages, profile):
    if not isinstance(instructions, str) or not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise Unavailable('invalid_input')
    if len(instructions) > 32768:
        raise Unavailable('input_too_large')
    for message in messages:
        if not isinstance(message, dict) or set(message) != {'role', 'content'}:
            raise Unavailable('invalid_input')
        if message['role'] not in ('user', 'assistant') or not isinstance(message['content'], str):
            raise Unavailable('invalid_input')
        if len(message['content']) > 32768:
            raise Unavailable('input_too_large')
    payload = {'instructions': instructions, 'messages': messages}
    # Text-only byte-tokenizer upper bound, including JSON escaping. The fixed
    # schema/message framing has another 4096 tokens of headroom. No tools,
    # images, attachments, external context, or caller-supplied schemas.
    if len(json.dumps(payload, ensure_ascii=True).encode()) + 4096 > profile['max_input_tokens']:
        raise Unavailable('input_too_large')
    return payload
