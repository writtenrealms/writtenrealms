"""Read-only, keyset-paginated inspection of one runtime's item policy."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.contrib.contenttypes.models import ContentType

from config import game_settings
from spawns.instance_clock import gameplay_now, is_time_controlled
from spawns.models import Item, Player, Mob, Equipment
from spawns.item_lifecycle import _chains
from worlds.models import World


class Command(BaseCommand):
    help = 'Preview item upkeep in a runtime world without modifying it.'

    def add_arguments(self, parser):
        parser.add_argument('world_id', type=int)
        parser.add_argument('--after-id', type=int, default=0)
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--due', action='store_true', help='Only timed, overdue candidates.')
        parser.add_argument('--explain', action='store_true', help='Include the due-query plan (no ANALYZE).')

    def handle(self, *args, **options):
        world = World.objects.filter(pk=options['world_id'], context__isnull=False).first()
        if world is None:
            raise CommandError('Provide a runtime world ID, not an authored world ID.')
        if not 1 <= options['limit'] <= 100:
            raise CommandError('--limit must be between 1 and 100.')
        now = gameplay_now(world)
        due = Item.objects.filter(world=world, upkeep_due_ts__lte=now, upkeep_retiring=False)
        query = due if options['due'] else Item.objects.filter(world=world)
        items = list(query.filter(pk__gt=options['after_id']).order_by('pk')[:options['limit']])
        root_ids = {item.upkeep_root_id for item in items if item.upkeep_root_id}
        counts = dict(Item.objects.filter(world=world, upkeep_root_id__in=root_ids)
                      .values('upkeep_root_id').annotate(n=Count('id')).values_list('upkeep_root_id', 'n'))
        chains = _chains(items)
        owned_types = {ct.pk for ct in ContentType.objects.get_for_models(Player, Mob, Equipment).values()}
        records = []
        for item in items:
            ancestors = chains[item.pk]
            reason = ('retiring' if any(i.upkeep_retiring for i in ancestors) else 'timed' if item.upkeep_due_ts
                      else 'unclaimed player equipment' if item.upkeep_kind == 'player_corpse' and not item.upkeep_empty_ts
                      else 'corpse contents' if any('corpse' in i.upkeep_kind for i in ancestors[1:])
                      else 'authored placement' if item.upkeep_placed
                      else 'persistent object' if item.is_persistent
                      else 'protected storage' if any(i.upkeep_storage or i.is_persistent for i in ancestors[1:])
                      else 'character possession' if ancestors[-1].container_type_id in owned_types
                      else 'awaiting empty corpse' if 'corpse' in item.upkeep_kind and not item.upkeep_empty_ts
                      else 'timer disabled' if item.upkeep_started_ts
                      else 'unclassified')
            records.append({'key': item.key, 'name': item.name, 'kind': item.upkeep_kind,
                'reason': reason, 'storage': item.upkeep_storage, 'policy': item.upkeep_policy,
                'deadline': item.upkeep_due_ts, 'revision': item.upkeep_revision,
                'overdue_seconds': max(0, (now-item.upkeep_due_ts).total_seconds()) if item.upkeep_due_ts else None,
                'root_key': f'item.{item.upkeep_root_id}' if item.upkeep_root_id else None,
                'tracked_root_rows': counts.get(item.upkeep_root_id)})
        output = {'runtime_world_id': world.pk, 'lifecycle': world.lifecycle, 'paused': is_time_controlled(world),
                  'item_worker_enabled': game_settings.ITEM_UPKEEP_ENABLED, 'gameplay_now': now,
                  'items': records, 'next_after_id': items[-1].pk if len(items) == options['limit'] else None}
        if options['explain']:
            output['due_query_plan'] = due.order_by('upkeep_due_ts', 'id')[:32].explain()
        self.stdout.write(json.dumps(output, default=str, indent=2))
