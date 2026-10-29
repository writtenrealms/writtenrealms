"""Bounded failure evidence, separate from prompts and gameplay relationships."""
import json
import re

from django.utils import timezone

from spawns.models import MobConversationFailure

RESPONSE_LIMIT = 4096


def speech_rejection(result):
    if not isinstance(result, dict) or not isinstance(result.get('text'), str):
        return 'missing_speech_text', {}
    text = result['text']
    if not text.strip():
        return 'empty_speech_text', {}
    if len(text) > 280:
        return 'speech_too_long', {'characters': len(text), 'maximum': 280}
    forbidden = list(re.finditer(r'[<>\[\]{}\x00-\x1f\x7f]', text))
    if forbidden:
        return 'forbidden_speech_characters', {
            'count': len(forbidden),
            'characters': [{'offset': match.start(), 'code_point': f'U+{ord(match.group()):04X}'}
                           for match in forbidden[:16]],
        }
    return None


def record_failure(mob, reason, *, request=None, details=None, response=None, truncated=False):
    details = dict(details or {})
    if request:
        details.update(request_status=request.status, provider=request.provider,
                       model=request.profile.get('model'), provider_error=request.error_code or None)
        if response is None and request.result is not None:
            rejected = request.result.get('rejection') if isinstance(request.result, dict) else None
            if rejected:
                response = rejected.get('response')
                truncated = rejected.get('truncated', False)
            elif isinstance(request.result, dict) and isinstance(request.result.get('text'), str):
                response = request.result['text']
            else:
                response = json.dumps(request.result, ensure_ascii=False)
    MobConversationFailure.objects.update_or_create(mob=mob, defaults={
        'occurred_at': timezone.now(), 'reason': reason, 'details': details,
        'request_id': request.pk if request else None,
        'response': response[:RESPONSE_LIMIT] if response is not None else None,
        'response_truncated': truncated or (response is not None and len(response) > RESPONSE_LIMIT),
    })
