from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import perf_counter
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from config import constants
from spawns.actions.base import ActionError
from spawns.actions.items import GetAction
from spawns.item_lifecycle import move_items, process_due_items, _retire
from spawns.models import Item, Player
from worlds.models import World


class ItemLifecycleConcurrencyTests(APITransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('upkeep-races@example.com', 'p')
        self.world = World.objects.new_world(name='Upkeep races', author=self.user)
        self.runtime = self.world.create_spawn_world()
        self.runtime.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
        self.room = self.world.zones.first().rooms.first()
        self.player = Player.objects.create(world=self.runtime, room=self.room, user=self.user, name='Looter')
        self.bag = Item.objects.create(world=self.runtime, container=self.room, name='a bag', type='container')
        self.loot = Item.objects.create(world=self.runtime, container=self.bag, name='a stone')
        self.now = self.bag.upkeep_due_ts+timedelta(seconds=1)
        self.enterContext(patch('spawns.events.flush_game_event_outbox'))

    @staticmethod
    def isolated(function):
        try:
            return function()
        finally:
            connections.close_all()

    def test_loot_claim_wins_and_worker_skips_locked_root(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Item.objects.select_for_update().get(pk=self.bag.pk)
                future = pool.submit(self.isolated, lambda: process_due_items(self.runtime.pk, now=self.now))
                result = future.result(timeout=10)
                self.assertGreater(result['skipped'], 0)
                move_items([self.bag], self.player)
        process_due_items(self.runtime.pk, now=self.now)
        self.assertEqual(Item.objects.filter(pk__in=[self.bag.pk, self.loot.pk], upkeep_due_ts__isnull=True).count(), 2)

    def test_retirement_wins_and_waiting_nested_transfer_is_rejected(self):
        claimed, release, attempting = Event(), Event(), Event()

        def hold(item, now):
            room_id = _retire(item, now)
            claimed.set()
            if not release.wait(10):
                raise RuntimeError('Test failed to release retirement')
            return room_id

        def recover():
            attempting.set()
            try:
                move_items([self.loot], self.player)
            except (ActionError, Item.DoesNotExist):
                return 'unavailable'
            return 'recovered'

        with ThreadPoolExecutor(max_workers=2) as pool, patch('spawns.item_lifecycle._retire', side_effect=hold):
            cleanup = pool.submit(self.isolated, lambda: process_due_items(self.runtime.pk, now=self.now, budget=1))
            try:
                self.assertTrue(claimed.wait(10))
                recovery = pool.submit(self.isolated, recover)
                self.assertTrue(attempting.wait(10))
            finally:
                release.set()
            cleanup.result(timeout=10)
            self.assertEqual(recovery.result(timeout=10), 'unavailable')
        self.assertTrue(Item.objects.filter(pk=self.bag.pk, upkeep_retiring=True).exists())
        for _ in range(3):
            process_due_items(self.runtime.pk, now=self.now)
        self.assertFalse(Item.objects.filter(pk__in=[self.bag.pk, self.loot.pk]).exists())

    def test_duplicate_world_worker_skips_claim_and_stop_blocks_new_cleanup(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                World.objects.select_for_update().get(pk=self.runtime.pk)
                result = pool.submit(self.isolated,
                    lambda: process_due_items(self.runtime.pk, now=self.now)).result(timeout=10)
                self.assertTrue(result['busy'])
                self.runtime.set_lifecycle(constants.WORLD_LIFECYCLE_STOPPED)
        process_due_items(self.runtime.pk, now=self.now)
        self.assertTrue(Item.objects.filter(pk=self.bag.pk, upkeep_retiring=False).exists())

    def test_128_looters_with_16_database_connections(self):
        players, items = [], []
        for index in range(128):
            players.append(Player.objects.create(world=self.runtime, room=self.room,
                user=self.user, name=f'Looter {index}'))
            items.append(Item.objects.create(world=self.runtime, container=self.room,
                name=f'token{index}', keywords=f'token{index}'))

        def get(pair):
            player, item = pair
            started = perf_counter()
            GetAction().execute(player.pk, item.name, build_events=False)
            return (perf_counter()-started)*1000

        started = perf_counter()
        with ThreadPoolExecutor(max_workers=16) as pool:
            durations = list(pool.map(lambda pair: self.isolated(lambda: get(pair)), zip(players, items)))
        from django.contrib.contenttypes.models import ContentType
        self.assertEqual(Item.objects.filter(pk__in=[i.pk for i in items],
            container_type=ContentType.objects.get_for_model(Player), upkeep_due_ts__isnull=True).count(), 128)
        durations.sort()
        print('Upkeep concurrent loot profile:', {'commands':128, 'connections':16,
              'total_ms':round((perf_counter()-started)*1000), 'p50_ms':round(durations[64]),
              'p95_ms':round(durations[121])})
