"""Bounded, cached platform analytics; never run on a game-processing path."""
from datetime import datetime, time, timedelta, timezone as datetime_timezone

from django.core.cache import cache
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.views import APIView

from config import constants
from core.permissions import IsStaffUser
from spawns.models import Player, PlayerEvent
from users.models import User
from worlds.models import World


OVERVIEW_CACHE_KEY = 'staff:core-overview:v1'
OVERVIEW_CACHE_SECONDS = 15
WORLD_LIMIT = 50
CHARACTER_LIMIT = 100


def build_overview():
    now = timezone.now()
    cutoff = now - timedelta(hours=24)
    today = now.astimezone(datetime_timezone.utc).date()
    first_day = today - timedelta(days=13)
    first_timestamp = datetime.combine(first_day, time.min, datetime_timezone.utc)
    users = User.objects.filter(is_temporary=False)
    players = Player.objects.filter(user__is_temporary=False, pending_deletion_ts__isnull=True)
    online = players.filter(in_game=True, world__lifecycle=constants.WORLD_STATE_RUNNING)
    roots = World.objects.filter(context__isnull=True, instance_of__isnull=True)
    running = World.objects.filter(context__isnull=False, lifecycle=constants.WORLD_STATE_RUNNING)
    entries = PlayerEvent.objects.filter(
        event=constants.PLAYER_EVENT_LOGIN, player__user__is_temporary=False,
        created_ts__gte=first_timestamp,
    )
    user_counts = users.aggregate(users=Count('pk'), signups_24h=Count('pk', filter=Q(date_joined__gte=cutoff)))
    online_counts = online.aggregate(online_characters=Count('pk'), online_users=Count('user_id', distinct=True))
    root_counts = roots.aggregate(root_worlds=Count('pk'), public_worlds=Count('pk', filter=Q(is_public=True)))
    counts = {
        **user_counts, **online_counts, **root_counts,
        'characters': players.count(),
        'running_worlds': running.count(),
        'active_users_24h': PlayerEvent.objects.filter(
            event=constants.PLAYER_EVENT_LOGIN, player__user__is_temporary=False,
            created_ts__gte=cutoff,
        ).order_by().values('player__user_id').distinct().count(),
    }
    signups = {
        row['day']: row['total'] for row in users.filter(date_joined__gte=first_timestamp)
        .annotate(day=TruncDate('date_joined', tzinfo=datetime_timezone.utc))
        .order_by().values('day').annotate(total=Count('pk'))
    }
    activity = {
        row['day']: row['total'] for row in entries
        .annotate(day=TruncDate('created_ts', tzinfo=datetime_timezone.utc))
        .order_by().values('day').annotate(total=Count('player__user_id', distinct=True))
    }
    days = [first_day + timedelta(days=offset) for offset in range(14)]
    worlds = running.annotate(online_characters=Count('players', filter=Q(
        players__in_game=True, players__user__is_temporary=False,
        players__pending_deletion_ts__isnull=True,
    ))).select_related('context').order_by('-online_characters', 'id')[:WORLD_LIMIT]
    characters = online.select_related('user', 'world__context').order_by('-last_connection_ts', 'id')[:CHARACTER_LIMIT]
    return {
        'generated_at': now.isoformat(),
        'cache_seconds': OVERVIEW_CACHE_SECONDS,
        'counts': counts,
        'activity': [{'date': day.isoformat(), 'signups': signups.get(day, 0), 'active_users': activity.get(day, 0)} for day in days],
        'online_worlds': [{
            'id': world.id, 'name': world.name, 'root_world_id': world.context_id,
            'state': world.lifecycle, 'online_characters': world.online_characters,
            'maintenance_mode': world.maintenance_mode or world.context.maintenance_mode,
            'changed_at': world.lifecycle_change_ts or world.change_state_ts,
        } for world in worlds],
        'online_characters': [{
            'id': player.id, 'name': player.name, 'level': player.level,
            'user_id': player.user_id, 'username': player.user.name,
            'world_id': player.world_id, 'world_name': player.world.name,
            'root_world_id': player.world.context_id,
            'last_connection_ts': player.last_connection_ts, 'last_action_ts': player.last_action_ts,
        } for player in characters],
        'limits': {'online_worlds': WORLD_LIMIT, 'online_characters': CHARACTER_LIMIT},
        'definitions': {
            'activity': 'Distinct accounts entering a world, by UTC day. This measures entries, not session duration or all activity.',
            'online': 'Characters marked in game in running worlds. Disconnected sessions may remain until connection cleanup.',
        },
    }


class StaffOverview(APIView):
    permission_classes = (IsAuthenticated, IsStaffUser)

    def get(self, request):
        data = cache.get(OVERVIEW_CACHE_KEY)
        if data is None:
            data = build_overview()
            cache.set(OVERVIEW_CACHE_KEY, data, OVERVIEW_CACHE_SECONDS)
        return Response(data)


class StaffAudit(APIView):
    permission_classes = (IsAuthenticated, IsStaffUser)

    def get(self, request):
        from system.models import StaffAuditEvent
        try:
            offset = int(request.query_params.get('offset', 0))
            if not 0 <= offset <= 100000:
                raise ValueError
        except (TypeError, ValueError):
            raise serializers.ValidationError({'offset': 'Must be an integer between 0 and 100000.'})
        events = StaffAuditEvent.objects.order_by('-created_ts', '-pk')
        return Response({
            'count': events.count(), 'offset': offset, 'limit': 50,
            'results': [{
                'id': row.id, 'actor': row.actor_email, 'action': row.action,
                'target': row.target, 'details': row.details, 'created_at': row.created_ts,
            } for row in events[offset:offset + 50]],
        })
