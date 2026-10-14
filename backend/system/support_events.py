"""Explicit low-volume support history, never a transcript of gameplay."""
from datetime import timedelta
import logging

from django.db import transaction
from django.utils import timezone

from system.models import SupportEvent

RETENTION_DAYS = 30
PRUNE_BATCH_SIZE = 1000
PRUNE_MAX_BATCHES = 20

# Only these small, scalar fields may enter the archive. In particular, do not
# pass command/request payloads, exception messages, tokens, IPs, or chat text.
DETAIL_FIELDS = {
    'character.created': set(),
    'character.deleted': set(),
    'session.login': set(),
    'session.logout': {'idle_seconds'},
    'instance.reserved': {'return_world_id', 'return_room_id'},
    'instance.entered': {'origin_room_id', 'return_world_id', 'return_room_id'},
    'instance.left': {'destination_world_id', 'destination_room_id'},
    'instance.cleanup': set(),
    'quest.started': {'quest_id', 'quest_slug', 'quest_instance_id'},
    'quest.resolved': {'quest_id', 'quest_slug', 'quest_instance_id'},
    'onboarding.rewards_granted': {'quest_id', 'quest_slug', 'quest_instance_id'},
    'onboarding.rewards_skipped': {'quest_id', 'quest_slug', 'quest_instance_id'},
    'player.died': {'destination_world_id', 'destination_room_id', 'death_sequence',
                    'routing_source', 'fallback_reason', 'corpse_id'},
}


def record_support_event(kind, *, player=None, world=None, reason='', details=None,
                         room_id=None):
    """One context SELECT and one INSERT, inside the caller's transaction.

    Snapshot IDs before deletion and use wall time even in a paused instance.
    There are no shared counters, cache writes, tasks, or gameplay subscriptions.
    """
    from worlds.models import World

    allowed = DETAIL_FIELDS[kind]
    details = details or {}
    if set(details) - allowed:
        raise ValueError('Unsupported support event details.')
    if any(type(value) not in (str, int, bool, float, type(None)) for value in details.values()):
        raise ValueError('Support event details must be scalar.')
    details = {key: value[:160] if isinstance(value, str) else value
               for key, value in details.items()}
    world_id = world.pk if world is not None else player.world_id
    context = World.objects.filter(pk=world_id).values(
        'name', 'context_id', 'context__instance_of_id', 'instance_run__id',
    ).get()
    template_id = context['context_id'] if context['context__instance_of_id'] else None
    return SupportEvent.objects.create(
        kind=kind, reason=reason[:64],
        player_id=player.pk if player is not None else None,
        user_id=player.user_id if player is not None else None,
        player_name=player.name[:100] if player is not None else '',
        world_id=world_id, world_name=context['name'][:100],
        base_world_id=context['context__instance_of_id'] or context['context_id'] or world_id,
        template_world_id=template_id, run_id=context['instance_run__id'],
        room_id=(room_id if room_id is not None else
                 player.room_id if player is not None and player.world_id == world_id else None),
        details=details,
    )


def record_quest_event(kind, *, player, quest, reason):
    return record_support_event(kind, player=player, reason=reason, details={
        'quest_id': quest.template_id,
        'quest_slug': quest.template.slug,
        'quest_instance_id': quest.pk,
    })


def prune_support_events(*, now=None, batch_size=PRUNE_BATCH_SIZE,
                         max_batches=PRUNE_MAX_BATCHES):
    cutoff = (now or timezone.now()) - timedelta(days=RETENTION_DAYS)
    deleted = 0
    for _ in range(max_batches):
        with transaction.atomic():
            ids = list(SupportEvent.objects.filter(created_ts__lt=cutoff)
                       .order_by('created_ts', 'id').select_for_update(skip_locked=True)
                       .values_list('id', flat=True)[:batch_size])
            if not ids:
                break
            deleted += SupportEvent.objects.filter(pk__in=ids).delete()[0]
        if len(ids) < batch_size:
            break
    remaining = SupportEvent.objects.filter(created_ts__lt=cutoff).exists()
    if remaining:
        logging.getLogger(__name__).warning('Support event retention backlog; next hourly batch will continue.')
    return {'deleted': deleted, 'backlog': remaining}
