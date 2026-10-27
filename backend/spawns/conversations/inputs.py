"""Trusted command metadata; never accepted from client-supplied event data."""
from dataclasses import replace
import uuid

from django.conf import settings
from builders.models import MobConversationPolicy
from spawns.models import CommunicationSession, MobConversationFocus

INPUT_KEY = '_conversation_input'
DELIVERY_KEY = '_conversation_delivery'
AUTHORED_KEY = '_conversation_authored_speakers'


def voluntary(ctx):
    return (ctx.actor_type == 'player' and not any((ctx.script_source, ctx.trigger_step,
            ctx.builder_force, ctx.capture_only)))


def stamp(ctx, events):
    if not settings.LLM_API_KEY or not voluntary(ctx) or not events:
        return events
    if events[0].type == 'cmd.say.success':
        focus = MobConversationFocus.objects.filter(player_id=ctx.actor_id, mob__isnull=False).values(
            'generation', 'world_session').first()
        if focus is None:
            return events
        session = focus['world_session']
        focus_generation = str(focus['generation'])
    else:
        definition_id = (events[0].data.get('target') or {}).get('definition_id')
        configured = MobConversationPolicy.objects.filter(enabled=True,
            world__mob_definitions__id=definition_id, world__mob_definitions__conversation__isnull=False).exists()
        if not configured:
            if not MobConversationFocus.objects.filter(player_id=ctx.actor_id, mob__isnull=False).exists():
                return events
        session = CommunicationSession.objects.get_or_create(world_id=ctx.actor.world_id)[0].generation
        focus_generation = ''
    request_id = ctx.payload.get('_request_id')
    identity = (uuid.uuid5(uuid.NAMESPACE_URL, f'conversation:{ctx.actor_key}:{request_id}:{ctx.payload.get("_request_segment", "r")}')
                if request_id else uuid.uuid4())
    data = {**events[0].data, INPUT_KEY: {
        'id': str(identity),
        'world_id': ctx.actor.world_id, 'room_id': ctx.actor.room_id,
        'location_sequence': ctx.actor.location_sequence,
        'disconnection': str(ctx.actor.last_disconnection_ts),
        'session': str(session), 'focus': focus_generation,
    }}
    return [replace(events[0], data=data), *events[1:]]
