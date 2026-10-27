"""Validation for authored mob dialogue, shared by models and YAML imports."""
import json

from core.condition_dsl import validate_condition_payload


def world_conversation_knowledge(value):
    """Normalize the authored world section; omission is handled by the importer."""
    if value is None:
        return ''
    if not isinstance(value, dict) or set(value) - {'knowledge'}:
        raise ValueError('spec.conversation must be null or a mapping containing only knowledge.')
    knowledge = value.get('knowledge', '')
    if not isinstance(knowledge, str) or len(knowledge) > 800:
        raise ValueError('spec.conversation.knowledge must be text of at most 800 characters.')
    return knowledge


def validate_conversation(value, *, field_name='conversation'):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'knowledge', 'greeting', 'when'}:
        raise ValueError(f'{field_name} must be null or a mapping containing only knowledge, greeting, and when.')
    for field, limit in [('knowledge', 400), ('greeting', 120)]:
        text = value.get(field, '')
        if not isinstance(text, str) or len(text) > limit or any(c in text for c in '<>\x1b'):
            raise ValueError(f'{field_name}.{field} must be plain text of at most {limit} characters.')
    condition = value.get('when', {})
    try:
        condition_size = len(json.dumps(condition, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name}.when must contain JSON-compatible values.') from exc
    if not isinstance(condition, (dict, bool)) or condition_size > 1024:
        raise ValueError(f'{field_name}.when must be a bounded condition DSL object or boolean.')
    validate_condition_payload(condition, field_name=f'{field_name}.when')
    return value
