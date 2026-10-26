"""Deterministic conversation focus, admission, and public speech.

Lock order is runtime clock, player, focus, nonblocking mob, allowance, inference control.
No lock survives a provider request. Only active focuses enter the bounded poll.
"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta, timezone as datetime_timezone
import re
import uuid

from django.db import DatabaseError, transaction
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from builders.models import MobConversationPolicy
from core.condition_dsl import ConditionContext, evaluate_condition
from inference import service as inference
from inference.configuration import Unavailable, validate_payload
from inference.models import InferenceControl, InferenceRequest
from quests.services.interactions import resolve_talk_interaction
from spawns.conversations.inputs import AUTHORED_KEY, DELIVERY_KEY, INPUT_KEY
from spawns.conversations.policy import GREETING, prompt
from spawns.events import GameEvent, _enqueue_game_event_batch, flush_game_event_outbox_batch
from spawns.instance_clock import clock_guard, current_simulation
from spawns.models import (
    CombatParticipant, CommunicationSession, EventSubscriptionReceipt, Mob,
    GameEventOutbox, MobConversationAllowance, MobConversationFocus, Player,
)

IDLE = timedelta(seconds=90)
HARD_LIMIT = timedelta(minutes=5)
COOLDOWN = timedelta(minutes=30)
NOTICE_INTERVAL = timedelta(seconds=15)
MAX_TURNS = 6
NPC_DAILY_LIMIT = 12


def _base_world(player):
    world = player.world.context or player.world
    return world.instance_of_id or world.pk


def _player(player_id):
    return Player.objects.select_for_update(of=('self',)).select_related(
        'user', 'room__zone', 'world__context', 'world__instance_of', 'core_faction',
    ).filter(pk=player_id).first()


def _mob(mob_id):
    # Don't wait behind a movement/combat mutation while holding a player lock.
    return Mob.objects.select_for_update(of=('self',), skip_locked=True).select_related('definition').filter(pk=mob_id).first()


def _policy(player):
    return MobConversationPolicy.objects.filter(world_id=_base_world(player), enabled=True).first()


def _current_session(world_id):
    return CommunicationSession.objects.filter(world_id=world_id).values_list('generation', flat=True).first()


def _input_matches(player, data):
    stamp = data.get(INPUT_KEY) or {}
    return (stamp.get('world_id') == player.world_id and stamp.get('room_id') == player.room_id
            and stamp.get('location_sequence') == player.location_sequence
            and stamp.get('disconnection') == str(player.last_disconnection_ts)
            and stamp.get('session') == str(_current_session(player.world_id)))


def _consume(data):
    try:
        event_id = uuid.UUID(str(data[INPUT_KEY]['id']))
    except (KeyError, ValueError):
        return False
    return EventSubscriptionReceipt.objects.get_or_create(
        event_id=event_id, subscriber='mob_conversations')[1]


def _eligible(player, mob, policy, *, focus=None, speech=None, check_quests=True, check_expiry=True):
    if (not player or not mob or not policy or not player.in_game or player.health <= 0
            or player.is_muted or player.user.is_muted or player.state != 'standing'
            or mob.health <= 0 or mob.is_pending_deletion or not mob.talkable
            or not mob.definition or not mob.definition.talkable or mob.definition.mob_type != 'humanoid'
            or player.world_id != mob.world_id or player.room_id != mob.room_id):
        return False
    spec = policy.mobs.get(mob.definition.slug)
    if spec is None:
        return False
    combat = CombatParticipant.objects.filter(Q(player_id=player.pk) | Q(mob_id=mob.pk))
    combat_gate = Q(is_active=True)
    if focus:
        combat_gate |= Q(created_ts__gte=focus.started_at)
    if combat.filter(combat_gate).exists():
        return False
    if not evaluate_condition(spec.get('when', {}), context=ConditionContext(
        actor=player, player=player, room=player.room, zone=player.room.zone, world=player.world,
    )):
        return False
    if focus and (focus.policy_revision != policy.revision or focus.world_id != player.world_id
            or focus.room_id != player.room_id or focus.player_location_sequence != player.location_sequence
            or focus.mob_move_sequence != mob.follow_move_sequence
            or focus.player_disconnection_ts != player.last_disconnection_ts
            or focus.world_session != _current_session(player.world_id)
            or (check_expiry and (focus.expires_at <= timezone.now() or focus.hard_expires_at <= timezone.now()))):
        return False
    if check_quests and resolve_talk_interaction(player, {'target': {
        'id': mob.pk, 'key': mob.key, 'definition_id': mob.definition_id,
    }}).handled:
        return False
    if speech:
        from spawns.triggers import mob_owns_speech
        if mob_owns_speech(player, mob, speech):
            return False
    return True


def _allowance(player, mob, now):
    owner = f'link:{player.user.link_id}' if player.user.link_id else f'user:{player.user_id}'
    day = now.astimezone(datetime_timezone.utc).date()
    row, _ = MobConversationAllowance.objects.select_for_update().get_or_create(
        owner=owner, definition_id=mob.definition_id, defaults={'day': day, 'touched_at': now})
    if row.day != day:
        row.day, row.used = day, 0
    if row.last_turn_at is None or row.last_turn_at + COOLDOWN <= now:
        row.attention = 0
    row.touched_at = now
    row.save()
    return row


def _emit(events, *, player=None, mob=None, focus=None):
    if focus is not None:
        delivery = {'player_id': player.pk, 'mob_id': mob.pk, 'focus': str(focus.generation),
                    'world_id': player.world_id, 'room_id': player.room_id,
                    'location_sequence': player.location_sequence, 'mob_sequence': mob.follow_move_sequence,
                    'disconnection': str(player.last_disconnection_ts), 'session': str(focus.world_session),
                    'policy_revision': str(focus.policy_revision), 'policy_world': _base_world(player),
                    'inference_generation': focus.inference_generation,
                    'until': min(focus.expires_at, focus.hard_expires_at).isoformat()}
    else:
        delivery = {'generated': True}
    events = [replace(event, data={**event.data, DELIVERY_KEY: delivery}) for event in events]
    _, batch_id = _enqueue_game_event_batch(events)
    if batch_id:
        if focus is not None:
            focus.publication_batch = batch_id
            focus.save(update_fields=['publication_batch'])
        transaction.on_commit(lambda: flush_game_event_outbox_batch(batch_id), robust=True)


def _refuse(player, mob, allowance, now):
    if allowance.last_notice_at and allowance.last_notice_at + NOTICE_INTERVAL > now:
        return
    from spawns.actions.communication import EmoteAction
    _emit(EmoteAction().execute(mob, 'turns away, unwilling to talk.').events)
    allowance.last_notice_at = now
    allowance.save(update_fields=['last_notice_at'])


def _close(focus, *, invalidate=True):
    if focus is None:
        return
    if focus.pending_request_id:
        inference.cancel(focus.pending_request_id)
    focus.mob = None
    focus.pending_request = None
    focus.transcript = []
    focus.publication_batch = None
    if invalidate:
        focus.generation = uuid.uuid4()
    focus.save(update_fields=['mob', 'pending_request', 'transcript', 'generation', 'publication_batch'])


def _expire(player, mob, policy, focus, *, speech=None):
    """Close once under the focus lock; only a still-present listener gets a notice."""
    now = timezone.now()
    if focus.expires_at > now and focus.hard_expires_at > now:
        return False
    if _eligible(player, mob, policy, focus=focus, speech=speech, check_expiry=False):
        _emit([GameEvent(type='notification.mob.conversation', recipients=[player.key], data={},
                         text=f'{mob.name} turns away, no longer listening to you.')])
    _close(focus)
    return True


@contextmanager
def _guard(player_id):
    world = Player.objects.filter(pk=player_id).values_list('world_id', flat=True).first()
    if world is None or current_simulation() is not None:
        yield None
        return
    with clock_guard(world) as run, transaction.atomic():
        player = _player(player_id)
        yield player if not (run and run.time_paused) else None


@transaction.atomic
def stop(player_id):
    if Player.objects.select_for_update().filter(pk=player_id).only('id').first():
        _close(MobConversationFocus.objects.select_for_update().filter(player_id=player_id).first())


def greet(player_id, event_data, *, authored=False):
    with _guard(player_id) as player:
        if not player or not _input_matches(player, event_data):
            return
        focus = MobConversationFocus.objects.select_for_update().filter(player=player).first()
        target_id = (event_data.get('target') or {}).get('id')
        if authored:
            _close(focus)
            return
        mob = _mob(target_id)
        if not mob:
            _close(focus)
            return
        policy = _policy(player)
        # Non-pilot definitions keep their existing authored-only TALK behavior.
        if not policy or not mob.definition or mob.definition.slug not in policy.mobs:
            _close(focus)
            return
        now = timezone.now()
        allowance = _allowance(player, mob, now)
        if focus and focus.mob_id == mob.pk and _eligible(player, mob, policy, focus=focus):
            return  # Repeated TALK neither greets again nor renews the lease.
        _close(focus)
        if not _eligible(player, mob, policy) or allowance.used >= NPC_DAILY_LIMIT or allowance.attention >= MAX_TURNS:
            _refuse(player, mob, allowance, now)
            return
        if MobConversationFocus.objects.filter(mob_id=mob.pk).exclude(player=player).exists():
            _refuse(player, mob, allowance, now)
            return
        try:
            capacity = inference.capacity(user_id=player.user_id, world_id=_base_world(player), purpose='mob_conversation')
        except (Unavailable, DatabaseError):
            _refuse(player, mob, allowance, now)
            return
        if allowance.last_notice_at and allowance.last_notice_at + NOTICE_INTERVAL > now:
            return
        if not _consume(event_data):
            return
        greeting = policy.mobs[mob.definition.slug].get('greeting') or GREETING
        focus, _ = MobConversationFocus.objects.update_or_create(player=player, defaults={
            'mob': mob, 'generation': uuid.uuid4(), 'world_id': player.world_id, 'room_id': player.room_id,
            'world_session': _current_session(player.world_id), 'policy_revision': policy.revision,
            'inference_generation': capacity['generation'], 'player_location_sequence': player.location_sequence,
            'mob_move_sequence': mob.follow_move_sequence, 'player_disconnection_ts': player.last_disconnection_ts,
            'started_at': now, 'expires_at': now + IDLE, 'hard_expires_at': now + HARD_LIMIT,
            'pending_request': None, 'turns': 0, 'last_notice_at': None,
            'transcript': [{'role': 'assistant', 'content': greeting}],
        })
        from spawns.actions.communication import SayAction
        _emit(SayAction().execute(mob, greeting, metadata={'source': 'mob_conversation', 'kind': 'greeting'}).events,
              player=player, mob=mob, focus=focus)
        allowance.last_notice_at = now
        allowance.save(update_fields=['last_notice_at'])


def hear(player_id, event_data, *, authored=False):
    with _guard(player_id) as player:
        if not player or not _input_matches(player, event_data):
            return
        focus = MobConversationFocus.objects.select_for_update().filter(player=player, mob__isnull=False).first()
        if not focus or str(focus.generation) != event_data[INPUT_KEY].get('focus'):
            return
        if not _consume(event_data):
            return
        mob, policy = _mob(focus.mob_id), _policy(player)
        authored_mobs = event_data.get(AUTHORED_KEY, [])
        speech = str(event_data.get('text') or '')[:280]
        if authored or authored_mobs == 'replayed' or focus.mob_id in authored_mobs:
            _close(focus)
            return
        if _expire(player, mob, policy, focus, speech=speech):
            return
        if not _eligible(player, mob, policy, focus=focus, speech=speech):
            _close(focus)
            return
        now = timezone.now()
        if focus.pending_request_id or (focus.publication_batch and
                GameEventOutbox.objects.filter(batch_id=focus.publication_batch).exists()):
            if focus.last_notice_at is None or focus.last_notice_at + NOTICE_INTERVAL <= now:
                _emit([GameEvent(type='notification.mob.conversation', recipients=[player.key], data={},
                                 text='They have not answered you yet.')])
                focus.last_notice_at = now
                focus.save(update_fields=['last_notice_at'])
            return
        allowance = _allowance(player, mob, now)
        if allowance.used >= NPC_DAILY_LIMIT or allowance.attention >= MAX_TURNS or focus.turns >= MAX_TURNS:
            _refuse(player, mob, allowance, now)
            _close(focus)
            return
        try:
            capacity = inference.capacity(user_id=player.user_id, world_id=_base_world(player), purpose='mob_conversation')
            if capacity['generation'] != focus.inference_generation:
                raise Unavailable('changed_configuration')
            payload = prompt(player, mob, policy, focus.transcript, speech)
            # Keep the current input and approved packet; drop oldest transcript
            # pairs until the gateway's conservative bound fits the chosen model.
            while True:
                try:
                    validate_payload(payload['instructions'], payload['messages'], capacity['profile'])
                    break
                except Unavailable as error:
                    if str(error) != 'input_too_large' or len(payload['messages']) <= 2:
                        raise
                    del payload['messages'][1:min(3, len(payload['messages']) - 1)]
            identity = uuid.UUID(event_data[INPUT_KEY]['id'])
            admission = inference.submit(request_id=identity, user_id=player.user_id,
                world_id=player.world_id, purpose='mob_conversation', **payload)
            if admission.reason:
                raise Unavailable(admission.reason)
        except (Unavailable, DatabaseError):
            _refuse(player, mob, allowance, now)
            _close(focus)
            return
        focus.pending_request_id = admission.request_id
        focus.transcript = [*focus.transcript[-5:], {'role': 'user', 'content': speech}]
        focus.turns += 1
        focus.expires_at = min(now + IDLE, focus.hard_expires_at)
        focus.save(update_fields=['pending_request', 'transcript', 'turns', 'expires_at'])
        allowance.used += 1
        allowance.attention += 1
        allowance.last_turn_at = now
        allowance.save(update_fields=['used', 'attention', 'last_turn_at'])


@contextmanager
def dispatch_guard(request_id):
    """Acquire gameplay locks before the gateway's accounting lock."""
    player_id = MobConversationFocus.objects.filter(pending_request_id=request_id).values_list('player_id', flat=True).first()
    if player_id is None:
        yield False
        return
    with _guard(player_id) as player:
        focus = MobConversationFocus.objects.select_for_update().filter(player_id=player_id, pending_request_id=request_id).first()
        if not player or not focus:
            yield False
            return
        mob, policy = _mob(focus.mob_id), _policy(player)
        speech = (focus.transcript[-1].get('content') if focus.transcript else '')
        if _expire(player, mob, policy, focus, speech=speech):
            yield False
            return
        allowed = _eligible(player, mob, policy, focus=focus, speech=speech)
        if not allowed:
            _close(focus)
        yield allowed


def _spoken_text(result):
    text = (result or {}).get('text', '')
    if not isinstance(text, str) or not 1 <= len(text) <= 280 or re.search(r'[<>\[\]{}\x00-\x1f\x7f]', text):
        return None
    return text.strip() or None


def finish(player_id):
    with _guard(player_id) as player:
        focus = MobConversationFocus.objects.select_for_update(of=('self',)).select_related('pending_request').filter(player_id=player_id).first()
        if not focus or not focus.mob_id:
            if focus and focus.pending_request_id:
                _close(focus)
            return
        mob, policy = _mob(focus.mob_id), _policy(player) if player else None
        speech = next((m['content'] for m in reversed(focus.transcript) if m['role'] == 'user'), '')
        if _expire(player, mob, policy, focus, speech=speech):
            return
        if not player or not _eligible(player, mob, policy, focus=focus, speech=speech):
            _close(focus)
            return
        request = focus.pending_request
        if request is None or request.status in inference.ACTIVE:
            return
        result = inference.result_for(request.pk)
        text = _spoken_text(result)
        if text is None:
            allowance = _allowance(player, mob, timezone.now())
            _refuse(player, mob, allowance, timezone.now())
            _close(focus)
            return
        from spawns.actions.communication import SayAction, EmoteAction
        focus.transcript = [*focus.transcript[-5:], {'role': 'assistant', 'content': text}]
        focus.pending_request = None
        focus.expires_at = min(timezone.now() + IDLE, focus.hard_expires_at)
        focus.save(update_fields=['transcript', 'pending_request', 'expires_at'])
        events = SayAction().execute(mob, text, metadata={
            'source': 'mob_conversation', 'request_id': str(request.pk),
        }).events
        allowance = _allowance(player, mob, timezone.now())
        closing = focus.turns >= MAX_TURNS or allowance.attention >= MAX_TURNS or allowance.used >= NPC_DAILY_LIMIT
        if closing:
            events += EmoteAction().execute(mob, 'turns back to other matters.').events
        _emit(events, player=player, mob=mob, focus=focus)
        if closing:
            _close(focus, invalidate=False)


def poll():
    ready = MobConversationFocus.objects.filter(
        Q(pending_request__isnull=False) & ~Q(pending_request__status__in=inference.ACTIVE) |
        Q(mob__isnull=False, expires_at__lte=timezone.now()),
    ).order_by('expires_at').values_list('player_id', flat=True)[:32]
    for player_id in list(ready):
        finish(player_id)


def prune():
    # Inputs belong to a focus lasting at most five minutes. Ten minutes keeps
    # replay protection beyond that lifetime without permanent spam receipts.
    receipt_cutoff = timezone.now() - timedelta(minutes=10)
    ids = list(EventSubscriptionReceipt.objects.filter(subscriber='mob_conversations',
               created_ts__lt=receipt_cutoff).order_by('created_ts').values_list('pk', flat=True)[:5000])
    EventSubscriptionReceipt.objects.filter(pk__in=ids).delete()
    cutoff = timezone.now() - timedelta(days=3)
    # Inactive transcripts were already erased on closure. Do not grow one
    # permanent relationship row per historical visitor/NPC pair.
    ids = list(MobConversationAllowance.objects.filter(touched_at__lt=cutoff)
               .order_by('touched_at').values_list('pk', flat=True)[:500])
    MobConversationAllowance.objects.filter(pk__in=ids, touched_at__lt=cutoff).delete()
    ids = list(MobConversationFocus.objects.filter(mob__isnull=True, pending_request__isnull=True,
               expires_at__lt=cutoff).order_by('expires_at').values_list('pk', flat=True)[:500])
    MobConversationFocus.objects.filter(pk__in=ids, mob__isnull=True, pending_request__isnull=True,
                                        expires_at__lt=cutoff).delete()


def delivery_allowed(data):
    """Last check for delayed outbox delivery; no provider call or game effects."""
    if data.get('generated'):
        return True
    if not settings.LLM_API_KEY:
        return False
    player = Player.objects.select_related('user', 'room__zone', 'world__context', 'core_faction').filter(pk=data['player_id'], in_game=True, health__gt=0,
        world_id=data['world_id'], room_id=data['room_id'], location_sequence=data['location_sequence']).first()
    if (not player or player.state != 'standing' or str(player.last_disconnection_ts) != data['disconnection']
            or timezone.now().isoformat() > data['until']):
        return False
    mob = Mob.objects.select_related('definition').filter(pk=data['mob_id']).first()
    focus = MobConversationFocus.objects.filter(player_id=player.pk, generation=data['focus']).first()
    policy = MobConversationPolicy.objects.filter(world_id=data['policy_world'], enabled=True,
                                                  revision=data['policy_revision']).first()
    control = InferenceControl.objects.filter(pk=1, enabled=True, generation=data['inference_generation']).filter(
        Q(circuit_until__isnull=True) | Q(circuit_until__lte=timezone.now())).exists()
    return bool(focus and control and _eligible(player, mob, policy, focus=focus))
