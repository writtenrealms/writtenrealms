"""Text-only adapters. Only the fenced inference worker calls this module."""
import json
from dataclasses import dataclass
from time import monotonic

import httpx
from django.conf import settings

from inference.configuration import RATE_KEYS


SCHEMA = {
    'type': 'object', 'properties': {'text': {'type': 'string'}},
    'required': ['text'], 'additionalProperties': False,
}
MAX_RESPONSE_BYTES = 262144


@dataclass(frozen=True)
class Outcome:
    usage: dict | None = None
    result: dict | None = None
    error: str = ''


def request_body(provider, profile, payload):
    common = {'model': profile['model']}
    if provider == 'openai':
        return {**common, 'store': False, 'service_tier': 'default',
            'input': [{'role': 'system', 'content': payload['instructions']}, *payload['messages']],
            'max_output_tokens': profile['max_output_tokens'],
            'text': {'format': {'type': 'json_schema', 'name': 'utterance', 'strict': True, 'schema': SCHEMA}}}
    return {**common, 'system': payload['instructions'], 'messages': payload['messages'],
        'max_tokens': profile['max_output_tokens'], 'service_tier': 'standard_only',
        'output_config': {'format': {'type': 'json_schema', 'schema': SCHEMA}}}


def parse_response(provider, data):
    try:
        raw = data['usage']
        if any(type(raw[key]) is not int for key in ('input_tokens', 'output_tokens')):
            raise ValueError()
        if provider == 'openai':
            details = raw.get('input_tokens_details') or {}
            cached = details.get('cached_tokens', 0)
            writes = details.get('cache_write_tokens', 0)
            usage = {'input': raw['input_tokens'] - cached - writes,
                     'output': raw['output_tokens'], 'cached_input': cached, 'cache_write': writes}
        else:
            usage = {'input': raw['input_tokens'], 'output': raw['output_tokens'],
                     'cached_input': raw.get('cache_read_input_tokens', 0),
                     'cache_write': raw.get('cache_creation_input_tokens', 0)}
        if any(type(usage[key]) is not int or not 0 <= usage[key] <= 10000000 for key in RATE_KEYS):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        return Outcome(error='unknown_usage')
    # Nonstandard service tiers and tool calls are outside the configured tariff.
    if (data.get('service_tier') not in (None, 'default', 'standard') or
            raw.get('service_tier') not in (None, 'default', 'standard')):
        return Outcome(error='unknown_billing_category')
    try:
        if provider == 'openai':
            if data.get('status') != 'completed':
                return Outcome(usage=usage, error='incomplete_output')
            output = data['output']
            if any(part['type'] not in ('message', 'reasoning') for part in output):
                return Outcome(error='unknown_billing_category')
            parts = [part for item in output if item['type'] == 'message' for part in item['content']]
            if len(parts) != 1 or parts[0]['type'] != 'output_text':
                return Outcome(usage=usage, error='invalid_output')
            value = parts[0]['text']
        else:
            if data.get('stop_reason') != 'end_turn':
                return Outcome(usage=usage, error='incomplete_output')
            parts = data['content']
            if any(part['type'] not in ('text', 'thinking', 'redacted_thinking') for part in parts):
                return Outcome(error='unknown_billing_category')
            texts = [part['text'] for part in parts if part['type'] == 'text']
            if len(texts) != 1:
                return Outcome(usage=usage, error='invalid_output')
            value = texts[0]
        result = json.loads(value)
        if (not isinstance(result, dict) or set(result) != {'text'} or
                not isinstance(result['text'], str) or not 1 <= len(result['text']) <= 4096):
            raise ValueError()
        return Outcome(usage=usage, result=result)
    except (KeyError, TypeError, ValueError, AttributeError):
        return Outcome(usage=usage, error='invalid_output')


def perform(provider, profile, payload):
    if not settings.LLM_NETWORK_ENABLED or not settings.LLM_WORKER:
        return Outcome(error='worker_not_enabled')
    key = settings.LLM_API_KEY
    if not key:
        return Outcome(error='missing_credentials')
    if provider == 'openai':
        url = 'https://api.openai.com/v1/responses'
        headers = {'Authorization': f'Bearer {key}'}
        if settings.LLM_OPENAI_PROJECT:
            headers['OpenAI-Project'] = settings.LLM_OPENAI_PROJECT
    else:
        url = 'https://api.anthropic.com/v1/messages'
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    try:
        deadline = monotonic() + 30
        # Explicitly no SDK/transport retries, redirects, or proxy environment.
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), trust_env=False,
                          follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
            with client.stream('POST', url, headers=headers, json=request_body(provider, profile, payload)) as response:
                if response.status_code != 200:
                    return Outcome(error='provider_http_error')
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES or monotonic() > deadline:
                        return Outcome(error='response_limit')
                data = json.loads(content)
        return parse_response(provider, data)
    except (httpx.HTTPError, ValueError, TypeError):
        # Do not log raw provider exceptions: they can include input or secrets.
        return Outcome(error='provider_unavailable')
