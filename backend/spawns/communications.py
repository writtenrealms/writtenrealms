"""Durable communication audit records and world-local question numbering."""

from datetime import timedelta
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Subquery
from django.utils import timezone

from spawns.models import CommunicationMessage, CommunicationSession, Player


def _retention_days():
    try:
        return max(1, int(settings.COMMUNICATION_RETENTION_DAYS))
    except (AttributeError, TypeError, ValueError):
        return 3


def record_communication(
    actor, channel, text, *, target=None, session=None, question_number=None,
    answer_to_question_number=None, in_reply_to=None, metadata=None,
):
    """Insert one record per utterance, without loading any recipients."""
    now = timezone.now()
    if in_reply_to is not None:
        # Reuse the question's author snapshot without another player lookup.
        target_data = dict(
            target_type=in_reply_to.sender_type, target_id=in_reply_to.sender_id,
            target_user_id=in_reply_to.sender_user_id, target_name=in_reply_to.sender_name,
        )
    else:
        target_data = dict(
            target_type=target.model_type if target is not None else '',
            target_id=target.pk if target is not None else None,
            target_user_id=target.user_id if isinstance(target, Player) else None,
            target_name=target.name if target is not None else '',
        )
    return CommunicationMessage.objects.create(
        created_ts=now,
        expires_at=now + timedelta(days=_retention_days()),
        world_id=actor.world_id,
        room_id=actor.room_id,
        channel=channel,
        sender_type=actor.model_type,
        sender_id=actor.pk,
        sender_user_id=actor.user_id if isinstance(actor, Player) else None,
        sender_name=actor.name,
        **target_data,
        text=text,
        session=session,
        question_number=question_number,
        answer_to_question_number=answer_to_question_number,
        in_reply_to_id=in_reply_to.pk if in_reply_to is not None else None,
        metadata=metadata or {},
    )


def reset_communication_session(world_id):
    """Start a fresh numbering epoch only when the game world starts."""
    with transaction.atomic():
        session, created = CommunicationSession.objects.select_for_update().get_or_create(
            world_id=world_id,
        )
        if not created:
            session.generation = uuid.uuid4()
            session.next_question_number = 1
            session.save(update_fields=['generation', 'next_question_number'])
        return session


def record_question(actor, text):
    """Allocate and log atomically; concurrent asks cannot reuse a number."""
    with transaction.atomic():
        session, _ = CommunicationSession.objects.select_for_update().get_or_create(
            world_id=actor.world_id,
        )
        question = record_communication(
            actor, 'ask', text, session=session.generation,
            question_number=session.next_question_number,
        )
        session.next_question_number += 1
        session.save(update_fields=['next_question_number'])
        return question


def resolve_question(world_id, question_number=None):
    """Find the requested (or latest) unexpired ask in this world's run."""
    current_session = CommunicationSession.objects.filter(
        world_id=world_id,
    ).values('generation')[:1]
    return _resolve_question(world_id, Subquery(current_session), question_number)


def _resolve_question(world_id, session, question_number):
    questions = CommunicationMessage.objects.filter(
        world_id=world_id,
        session=session,
        channel='ask',
        expires_at__gt=timezone.now(),
    )
    if question_number is not None:
        questions = questions.filter(question_number=question_number)
    return questions.order_by('-question_number').first()


def record_answer(actor, text, question_number=None):
    """Resolve and log while holding the same lock used by world restarts."""
    with transaction.atomic():
        session = CommunicationSession.objects.select_for_update().filter(
            world_id=actor.world_id,
        ).first()
        if session is None:
            return None
        question = _resolve_question(
            actor.world_id, session.generation, question_number,
        )
        if question is None:
            return None
        return record_communication(
            actor, 'answer', text, session=question.session,
            answer_to_question_number=question.question_number,
            in_reply_to=question,
        )


def prune_communication_messages(batch_size=None):
    """Delete one indexed, bounded page; overlapping workers skip row locks."""
    if batch_size is None:
        batch_size = getattr(settings, 'COMMUNICATION_PRUNE_BATCH_SIZE', 10_000)
    try:
        limit = max(1, min(int(batch_size), 10_000))
    except (TypeError, ValueError):
        limit = 10_000
    with transaction.atomic():
        message_ids = list(
            CommunicationMessage.objects.select_for_update(skip_locked=True)
            .filter(expires_at__lte=timezone.now())
            .order_by('expires_at', 'id')
            .values_list('id', flat=True)[:limit]
        )
        if not message_ids:
            return 0
        deleted, _ = CommunicationMessage.objects.filter(id__in=message_ids).delete()
        return deleted
