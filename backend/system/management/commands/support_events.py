"""Read a bounded support timeline as JSON lines, without loading live objects."""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from system.models import SupportEvent
from system.support_events import DETAIL_FIELDS, RETENTION_DAYS


class Command(BaseCommand):
    help = 'Read retained support events (newest first; timestamps are UTC).'

    def add_arguments(self, parser):
        parser.add_argument('--player', type=int)
        parser.add_argument('--user', type=int)
        parser.add_argument('--world', type=int, help='Base world ID, including its instances.')
        parser.add_argument('--run', type=int)
        parser.add_argument('--kind', choices=sorted(DETAIL_FIELDS))
        parser.add_argument('--since', help='ISO timestamp with timezone, e.g. 2026-10-14T00:00:00Z')
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, **options):
        if not 1 <= options['limit'] <= 1000:
            raise CommandError('--limit must be between 1 and 1000.')
        cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
        if options['since']:
            try:
                since = parse_datetime(options['since'])
            except ValueError:
                since = None
            if since is None or timezone.is_naive(since):
                raise CommandError('--since requires an ISO timestamp with timezone.')
            cutoff = max(cutoff, since)
        events = SupportEvent.objects.filter(created_ts__gte=cutoff)
        for option, field in [('player', 'player_id'), ('user', 'user_id'),
                              ('world', 'base_world_id'), ('run', 'run_id'), ('kind', 'kind')]:
            if options[option] is not None:
                events = events.filter(**{field: options[option]})
        for event in events.order_by('-created_ts', '-id').values()[:options['limit']]:
            self.stdout.write(json.dumps(event, default=str))
