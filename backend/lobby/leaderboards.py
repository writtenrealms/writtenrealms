"""Bounded, cached lobby rankings over canonical result records.

Cold queries aggregate only the selected world/template in PostgreSQL. A shared
refresh lease prevents concurrent lobby visitors from repeating the aggregation;
warm requests do not read players or result history. No work runs per game tick.
"""
import hashlib
import json
import time

from django.core.cache import cache
from django.db.models import Count, F, FloatField, Min, Q, ExpressionWrapper

from spawns.models import DuelParticipant, Player
from worlds.models import InstanceClearRecord, World


FRESH_SECONDS = 30


class LeaderboardsRefreshing(Exception):
    pass


def _identity(player):
    return {
        'id': player.pk, 'name': player.name, 'archetype': player.archetype,
        'core_faction': player.core_faction.name if player.core_faction_id else '',
    }


def _players(ids):
    return {p.pk: _identity(p) for p in Player.objects.filter(pk__in=ids).select_related('core_faction')}


def _glory(world, panel):
    players = Player.objects.filter(
        Q(world__context_id=world.pk) | Q(world__context__instance_of_id=world.pk),
        is_builder=False, pending_deletion_ts__isnull=True,
    ).select_related('core_faction').order_by('-glory', '-experience', '-created_ts', '-pk')[:panel.get('limit', 10)]
    return {
        'title': panel.get('title', 'Glory & Experience'),
        'description': 'Glory first, then experience',
        'empty_message': 'No ranked characters yet.',
        'entries': [{**_identity(p), 'glory': p.glory, 'experience': p.experience} for p in players],
    }


def _dueling(world, panel):
    minimum = panel.get('minimum_matches', 3)
    results = list(DuelParticipant.objects.filter(
        match__base_world_id=world.pk, match__status='completed',
        role='contestant', result__in=('won', 'lost'),
        player__is_builder=False, player__pending_deletion_ts__isnull=True,
    ).order_by().values('player_id').annotate(
        wins=Count('pk', filter=Q(result='won')), matches=Count('pk'),
    ).filter(matches__gte=minimum).annotate(
        win_percentage=ExpressionWrapper(100.0 * F('wins') / F('matches'), output_field=FloatField()),
    ).order_by('-win_percentage', '-wins', 'player_id')[:panel.get('limit', 10)])
    players = _players([r['player_id'] for r in results])
    return {
        'title': panel.get('title', 'Dueling'),
        'description': f'Win percentage · Minimum {minimum} completed match' + ('' if minimum == 1 else 'es'),
        'empty_message': 'No duelists qualify yet.',
        'entries': [{**players[r['player_id']], 'wins': r['wins'], 'losses': r['matches'] - r['wins'],
                     'matches': r['matches'], 'win_percentage': r['win_percentage']} for r in results if r['player_id'] in players],
    }


def _instance(world, panel, templates):
    template = templates.get(panel['instance'])
    result = {
        'title': panel.get('title', f'{template.name if template else panel["instance"]} — Fastest Clears'),
        'description': '', 'entries': [], 'empty_message': 'No qualifying clears yet.',
    }
    if template is None or not template.config.instance_goal:
        result['empty_message'] = 'This instance is currently unavailable.'
        return result
    timing = panel.get('timing', 'current')
    time_control = template.config.instance_time_control if timing == 'current' else timing == 'time_control'
    solo = template.config.instance_single_player
    result['description'] = ('Personal bests' if solo else 'Party clears') + ' · ' + (
        'Time control · Wall-clock time' if time_control else 'Continuous · Wall-clock time')
    records = InstanceClearRecord.objects.filter(
        template_world_id=template.pk, time_control=time_control,
        single_player=solo, ranking_eligible=True,
    )
    if solo:
        bests = list(records.filter(
            ranking_player__is_builder=False, ranking_player__pending_deletion_ts__isnull=True,
        ).order_by().values('ranking_player_id').annotate(
            clear_time_ms=Min('clear_time_ms'),
        ).order_by('clear_time_ms', 'ranking_player_id')[:panel.get('limit', 10)])
        players = _players([r['ranking_player_id'] for r in bests])
        result['entries'] = [{**players[r['ranking_player_id']], 'clear_time_ms': r['clear_time_ms']}
                             for r in bests if r['ranking_player_id'] in players]
    else:
        # Party runs are records, not individual personal bests. Snapshot names
        # survive cleanup; one bounded query returns at most the requested rows.
        result['entries'] = [
            {'id': r.pk, 'name': ', '.join(p['name'] for p in r.participants), 'clear_time_ms': r.clear_time_ms}
            for r in records.only('id', 'participants', 'clear_time_ms').order_by('clear_time_ms', 'completed_at', 'pk')[:panel.get('limit', 10)]
        ]
    return result


def world_leaderboard_panels(world):
    panels = world.config.leaderboards
    if not panels:
        return []
    slugs = {p['instance'] for p in panels if p['type'] == 'instance_clear_time'}
    templates = {w.instance_slug: w for w in World.objects.filter(
        instance_of_id=world.pk, context_id__isnull=True, instance_slug__in=slugs,
    ).exclude(lifecycle='archived').select_related('config')}
    # Configuration changes are visible immediately, including a template's
    # current timing/party mode. Cache access happens after lobby permissions.
    signature = [panels, [(w.pk, w.name, w.config.instance_time_control,
                           w.config.instance_single_player, w.config.instance_goal)
                          for w in sorted(templates.values(), key=lambda w: w.pk)]]
    digest = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:24]
    key = f'lobby-rankings:v1:{world.pk}:{digest}'
    cached = cache.get(key)
    if cached is not None and cached['fresh_until'] > time.time():
        return cached['panels']
    lease = f'{key}:refresh'
    if not cache.add(lease, True, timeout=60):
        if cached is not None:
            return cached['panels']
        raise LeaderboardsRefreshing()
    try:
        providers = {'glory_experience': _glory, 'dueling': _dueling}
        result = []
        for index, panel in enumerate(panels):
            data = _instance(world, panel, templates) if panel['type'] == 'instance_clear_time' else providers[panel['type']](world, panel)
            result.append({'id': str(index), 'type': panel['type'], **data})
        cache.set(key, {'fresh_until': time.time() + FRESH_SECONDS, 'panels': result}, timeout=300)
        return result
    finally:
        cache.delete(lease)
