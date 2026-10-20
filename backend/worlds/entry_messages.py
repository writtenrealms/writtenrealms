"""Presentation-only introduction text for players returning to an instance."""
from copy import deepcopy

from core.condition_dsl import (
    ConditionContext, evaluate_condition, prefetch_quest_conditions,
    validate_condition_payload,
)


def normalize_entry_message_conditions(value):
    if not isinstance(value, (dict, list, bool)):
        raise ValueError('entry_message_conditions must be a structured condition.')
    validate_condition_payload(value, field_name='entry_message_conditions')
    return deepcopy(value)


def returning_instance_message(player) -> str:
    """Never execute entry triggers or replay a scene outside its starting room."""
    if not player.world.context.instance_of_id:
        return ''
    config = player.world.config
    if player.room_id != config.starting_room_id or not config.entry_message.strip():
        return ''
    conditions = config.entry_message_conditions
    context = ConditionContext(actor=player, room=player.room, world=player.world)
    prefetch_quest_conditions([conditions], context=context)
    if not evaluate_condition(conditions, context=context):
        return ''
    return config.entry_message
