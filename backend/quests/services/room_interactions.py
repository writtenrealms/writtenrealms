"""Quest-owned room work, using the gameplay clock and durable event outbox.

One indexed cursor lives on the active quest attempt. Pollers select only due
work; player-first locks agree with ordinary quest commands and prevent retries
from completing a beat or paying a resolution twice.
"""
from copy import deepcopy
from datetime import timedelta
import logging
import uuid

from django.db import transaction, OperationalError, InterfaceError
from django.utils import timezone

from quests.models import QuestInstance
from quests.services.predicates import evaluate_condition
from quests.services.scope import instances_for_player
from spawns.events import GameEvent, enqueue_game_events, flush_game_event_outbox
from spawns.instance_clock import clock_guard, gameplay_now, is_time_controlled, in_simulation, live_worlds, serialized_world
from spawns.models import Player

logger = logging.getLogger(__name__)


def _active(player):
    return instances_for_player(player).filter(status='active', template__status='active').select_related('template')


def _spec(instance):
    from quests.services.engine import get_step
    step = get_step(instance.template, instance.current_step_id) or {}
    return step.get('interaction') if step.get('kind') == 'interaction' else None


def _running_here(instance, player):
    state = instance.interaction_state
    return bool(instance.interaction_due_at and state
                and state.get('room_id') == player.room_id
                and state.get('world_id') == player.world_id
                and state.get('location_sequence') == player.location_sequence)


def _eligible(instance, player, spec):
    room = player.room
    return bool(room and spec['room'] == f'room@{room.relative_id}'
                and room.world_id == instance.template.world_id
                and evaluate_condition(spec.get('conditions'), player=player,
                                       template=instance.template, quest_instance=instance))


def room_interaction_labels(player, room=None):
    """One joined quest query; no template or history query per candidate."""
    labels = []
    if room is not None and room.pk != player.room_id:
        return labels
    for instance in _active(player):
        spec = _spec(instance)
        if spec and not _running_here(instance, player) and _eligible(instance, player, spec):
            labels.append(spec['command'])
    return list(dict.fromkeys(labels))


def room_actions_event(player):
    from spawns.triggers import get_room_action_labels_for_actor
    from spawns.state_payloads import room_payload_key_for
    return GameEvent(type='notification.room.actions_changed', recipients=[player.key], data={
        'world_id': player.world_id, 'room_key': room_payload_key_for(player.room) if player.room_id else None,
        'actions_revision': timezone.now().timestamp(),
        'actions': get_room_action_labels_for_actor(player, player.room),
    })


def _text(player, text, instance):
    from quests.services.engine import _render_quest_string
    return GameEvent(type='quest.interaction.narration', recipients=[player.key], data={},
                     text=_render_quest_string(text, player=player, quest_instance=instance))


def _clear(instance):
    instance.interaction_state = {}
    instance.interaction_due_at = None
    instance.save(update_fields=['interaction_state', 'interaction_due_at', 'modified_ts'])


def _beat(instance, player, now):
    from quests.services.engine import enter_step
    state = instance.interaction_state
    spec = state['spec']
    index = state['index']
    events = [_text(player, spec['beats'][index]['text'], instance)]
    if index + 1 == len(spec['beats']):
        result = enter_step(instance, step_id=spec['goto'], player=player, entry_reason='interaction')
        return [*events, *result.events]
    state['index'] += 1
    # Preserve spacing after a delayed worker, rather than dumping every beat
    # at once. Manual instance advances drain each scheduled gameplay deadline.
    instance.interaction_due_at = now + timedelta(seconds=spec['beats'][index + 1]['after_seconds'])
    instance.save(update_fields=['interaction_state', 'interaction_due_at', 'modified_ts'])
    return events


@serialized_world(lambda player, text: player.world)
@transaction.atomic
def start_room_interaction(player, text):
    """Return None for an unrelated command; otherwise a quest command result."""
    from quests.services.engine import QuestRuntimeError, QuestTransitionResult, _assert_gameplay_advancing
    command = ' '.join(text.lower().split())
    # Most fallback commands are unrelated. Avoid acquiring a player lock for
    # them, then re-read authoritatively after taking the lock for an actual job.
    candidates = [q.pk for q in _active(player) if (_spec(q) or {}).get('command') == command]
    if not candidates:
        return None
    player = Player.objects.select_for_update(of=('self',)).select_related('room__zone', 'world').get(pk=player.pk)
    _assert_gameplay_advancing(player)
    matches = []
    for instance in _active(player).filter(pk__in=candidates).select_for_update(of=('self',)):
        spec = _spec(instance)
        if spec and spec['command'] == command and _eligible(instance, player, spec):
            matches.append((instance, spec))
    if not matches:
        raise QuestRuntimeError('You cannot do that quest action here right now.', code='interaction_unavailable')
    if len(matches) != 1:
        raise QuestRuntimeError('More than one quest offers that action here. The command needs a unique name.', code='interaction_ambiguous')
    instance, spec = matches[0]
    if _running_here(instance, player):
        raise QuestRuntimeError('You are already working on that. Wait for the work to finish.', code='interaction_running')
    now = gameplay_now(player.world)
    instance.interaction_state = {
        'token': uuid.uuid4().hex, 'step_id': instance.current_step_id,
        'room_id': player.room_id, 'world_id': player.world_id,
        'location_sequence': player.location_sequence, 'index': 0, 'spec': deepcopy(spec),
    }
    instance.interaction_due_at = now
    events = _beat(instance, player, now)
    # The last beat's enter_step already refreshes the room actions.
    if len(spec['beats']) > 1:
        events.append(room_actions_event(player))
    return QuestTransitionResult(quest_instance=instance, events=events)


def process_due_interactions(*, limit=100, now=None, world_id=None):
    due_at = now or timezone.now()
    candidates = live_worlds(QuestInstance.objects.filter(interaction_due_at__lte=due_at))
    if world_id is not None:
        candidates = candidates.filter(world_id=world_id)
    rows = list(candidates.order_by('interaction_due_at', 'id').values(
        'id', 'player_id', 'world_id', 'interaction_state',
    )[:limit])
    processed = 0
    for row in rows:
        with clock_guard(row['world_id']):
            if is_time_controlled(row['world_id']) and not in_simulation(row['world_id']):
                continue
            with transaction.atomic():
                player = Player.objects.select_for_update(skip_locked=True, of=('self',)).select_related('room__zone', 'world').filter(pk=row['player_id']).first()
                if player is None:
                    continue
                instance = QuestInstance.objects.select_for_update(of=('self',)).select_related('template').filter(
                    pk=row['id'], interaction_due_at__lte=due_at,
                ).first()
                if instance is None or instance.interaction_state != row['interaction_state']:
                    continue
                state = instance.interaction_state
                valid = (instance.status == 'active' and instance.template.status == 'active'
                         and state.get('step_id') == instance.current_step_id
                         and state.get('spec') == _spec(instance)
                         and _running_here(instance, player)
                         and _eligible(instance, player, state['spec']))
                if not valid:
                    _clear(instance)
                    events = [_text(player, 'Your work was interrupted. Completed quest steps are preserved.', instance), room_actions_event(player)]
                else:
                    try:
                        with transaction.atomic():
                            beat_time = instance.interaction_due_at if in_simulation(row['world_id']) else due_at
                            events = _beat(instance, player, beat_time)
                    except (OperationalError, InterfaceError):
                        raise
                    except Exception:
                        logger.exception('Quest interaction failed for attempt %s', instance.pk)
                        instance.refresh_from_db()
                        _clear(instance)
                        events = [_text(player, 'Your work could not finish. You can try the action again.', instance), room_actions_event(player)]
                enqueue_game_events(events)
                transaction.on_commit(flush_game_event_outbox, robust=True)
                processed += 1
    return {'processed': processed}
