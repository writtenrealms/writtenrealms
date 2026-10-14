from datetime import timedelta
import json
from time import perf_counter
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from config import constants
from spawns.item_lifecycle import process_due_items
from spawns.models import Item
from tests.base import WorldTestCase


class UpkeepScaleTests(WorldTestCase):
    def test_due_index_and_bounded_work_with_ten_thousand_retained_items(self):
        self.spawn_world.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
        # Unclassified/protected rows have no deadline and must not be loaded
        # by the sweep, irrespective of how long this runtime has been online.
        Item.objects.bulk_create([Item(world=self.spawn_world, container=self.room,
                                       name='retained') for _ in range(10000)], batch_size=1000)
        due = Item.objects.create(world=self.spawn_world, container=self.room, name='expired')
        now = due.upkeep_due_ts+timedelta(seconds=1)
        with connection.cursor() as cursor:
            cursor.execute('ANALYZE spawns_item')
        plan = Item.objects.filter(world=self.spawn_world, upkeep_retiring=False,
            upkeep_due_ts__lte=now).order_by('upkeep_due_ts','id')[:32].explain(format='json', analyze=True, buffers=True)
        self.assertIn('item_upkeep_due', plan)
        from spawns.tasks import dispatch_item_upkeep
        with patch('spawns.tasks.timezone.now', return_value=now), patch('spawns.tasks.run_item_upkeep.apply_async'), CaptureQueriesContext(connection) as discovery:
            dispatch_item_upkeep()
        discovery_sql = next(q['sql'] for q in discovery if q['sql'].startswith('SELECT')
                             and 'FOR UPDATE' in q['sql'] and 'worlds_world' in q['sql'])
        with connection.cursor() as cursor:
            cursor.execute('EXPLAIN (FORMAT JSON) ' + discovery_sql)
            discovery_plan = json.dumps(cursor.fetchone()[0])
        self.assertIn('item_upkeep_due', discovery_plan)
        self.assertIn('item_upkeep_retiring', discovery_plan)
        started = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            result = process_due_items(self.spawn_world.pk, now=now)
        self.assertEqual(result['retired'], 1)
        self.assertEqual(result['deleted'], 1)
        self.assertLess(len(queries), 45)
        self.assertEqual(Item.objects.filter(world=self.spawn_world).count(), 10000)
        print('Upkeep retained-population profile:', {'retained':10000, 'due':1,
              'queries':len(queries), 'elapsed_ms':round((perf_counter()-started)*1000),
              'query_plan':json.loads(plan)[0]['Plan']})
