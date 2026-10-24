from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITransactionTestCase

from builders.models import ItemBundle, ItemBundleEntry, ItemDefinition, SpawnEntry, SpawnPlan, SpawnPlanRun
from config import constants
from spawns.item_lifecycle import move_items
from spawns.item_stock import ItemStock
from spawns.models import Item, Player
from spawns.spawn_plans import run_spawn_plans
from tests.base import WorldTestCase
from worlds.models import Room, World


class StockFixture:
    def setup_stock(self):
        self.spawn_world.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
        self.definition = ItemDefinition.objects.create(world=self.world, slug='stock-sword', name='a sword')
        self.plan = SpawnPlan.objects.create(world=self.world, zone=self.zone, slug='stock',
            respawn_policy={'mode': 'fixed', 'seconds': 0})
        self.entry = SpawnEntry.objects.create(plan=self.plan, slug='sword',
            source='itemdefinition.stock-sword', target_room=self.room, count=1)
        self.reconcile(initial=True)

    def reconcile(self, **kwargs):
        return run_spawn_plans(world=self.spawn_world, **kwargs)[0]

    def floor(self):
        return self.room.inventory.filter(world=self.spawn_world, definition=self.definition,
                                          is_pending_deletion=False)

    def add_chest(self):
        chest = ItemDefinition.objects.create(world=self.world, slug='stock-chest', name='a chest', item_type='container')
        parent = SpawnEntry.objects.create(plan=self.plan, slug='chest', order=2,
            source='itemdefinition.stock-chest', target_room=self.room, count=1)
        SpawnEntry.objects.create(plan=self.plan, slug='contents', order=3,
            source='itemdefinition.stock-sword', target_entry=parent, count=2)
        self.reconcile()
        return parent, Item.objects.get(world=self.spawn_world, definition=chest)


class SpawnItemStockTests(StockFixture, WorldTestCase):
    def setUp(self):
        super().setUp()
        self.setup_stock()

    def test_full_chest_skips_refill_without_blocking_other_destinations(self):
        _, chest = self.add_chest()
        chest.capacity = 2
        chest.save(update_fields=['capacity'])
        move_items([chest.inventory.first()], self.player)
        deposit = Item.objects.create(world=self.spawn_world, container=chest, name='a deposit')
        move_items([self.floor().get()], self.player)
        result = self.reconcile()
        self.assertEqual(result['spawned'], 1)
        self.assertEqual(chest.inventory.count(), 2)
        self.assertEqual(self.floor().count(), 1)
        move_items([deposit], self.player)
        self.assertEqual(self.reconcile()['spawned'], 1)
        self.assertEqual(chest.inventory.count(), 2)

    def test_pickup_allows_replacement_and_return_before_poll_prevents_it(self):
        original = self.floor().get()
        provenance = original.spawn_placement_id
        move_items([original], self.player)
        move_items([original], self.room)
        original.refresh_from_db()
        deadline = original.upkeep_due_ts
        self.assertEqual(self.reconcile()['spawned'], 0)
        original.refresh_from_db()
        self.assertEqual(original.upkeep_due_ts, deadline)
        self.assertFalse(original.upkeep_placed)
        self.assertEqual(original.spawn_placement_id, provenance)
        move_items([original], self.player)
        self.assertEqual(self.reconcile()['spawned'], 1)
        self.assertEqual(self.floor().count(), 1)
        self.assertTrue(self.player.inventory.filter(pk=original.pk).exists())

    def test_return_after_replacement_keeps_surplus_without_spawning_again(self):
        original = self.floor().get()
        move_items([original], self.player)
        self.reconcile()
        move_items([original], self.room)
        self.assertEqual(self.reconcile()['spawned'], 0)
        self.assertEqual(self.floor().count(), 2)

    def test_inventory_bags_equipment_and_other_rooms_do_not_count(self):
        bag = Item.objects.create(world=self.spawn_world, container=self.player, name='a bag', type='container')
        other_room = self.create_imported_room(relative_id=100, x=1)
        for target in [bag, self.player.equipment, other_room]:
            with self.subTest(target=target):
                item = self.floor().get()
                move_items([item], target)
                self.assertEqual(self.reconcile()['spawned'], 1)
                self.assertEqual(self.floor().count(), 1)
                item.refresh_from_db()
                self.assertEqual(item.container, target)

    def test_floor_bag_is_not_the_room_destination(self):
        bag = Item.objects.create(world=self.spawn_world, container=self.room, name='a bag', type='container')
        move_items([self.floor().get()], bag)
        self.assertEqual(self.reconcile()['spawned'], 1)

    def test_unrelated_same_definition_does_not_satisfy_this_entry(self):
        original = self.floor().get()
        move_items([original], self.player)
        self.definition.spawn(self.room, self.spawn_world)
        self.assertEqual(self.reconcile()['spawned'], 1)
        self.assertEqual(self.floor().count(), 2)

    def test_other_entry_with_same_definition_has_independent_quota(self):
        original = self.floor().get()
        SpawnEntry.objects.create(plan=self.plan, slug='second-sword', order=2,
            source='itemdefinition.stock-sword', target_room=self.room, count=1)
        self.assertEqual(self.reconcile()['spawned'], 1)
        move_items([original], self.player)
        self.assertEqual(self.reconcile()['spawned'], 1)
        self.assertEqual(self.floor().count(), 2)

    def test_same_entry_counts_each_concrete_room_separately(self):
        other_room = self.create_imported_room(relative_id=100, x=1)
        self.entry.count = 2
        self.entry.save(update_fields=['count'])
        self.reconcile()
        first, second = list(self.floor().order_by('pk'))
        second.spawn_placement.room = other_room
        second.spawn_placement.save(update_fields=['room'])
        move_items([second], other_room)
        self.assertEqual(self.reconcile()['spawned'], 0)
        move_items([first], self.player)
        self.assertEqual(self.reconcile()['spawned'], 1)
        self.assertEqual(self.floor().count(), 1)
        self.assertEqual(other_room.inventory.filter(world=self.spawn_world).count(), 1)

    def test_two_generations_from_one_slot_satisfy_two_item_quota(self):
        first = self.floor().get()
        move_items([first], self.player)
        self.reconcile()
        second = self.floor().get()
        self.entry.count = 2
        self.entry.save(update_fields=['count'])
        move_items([first], self.room)
        self.assertEqual(first.spawn_placement_id, second.spawn_placement_id)
        self.assertEqual(self.reconcile()['spawned'], 0)
        self.assertEqual(self.floor().count(), 2)
        move_items([first], self.player)
        self.assertEqual(self.reconcile()['spawned'], 1)
        self.assertEqual(self.floor().count(), 2)

    def test_retiring_items_do_not_count(self):
        Item.objects.filter(pk=self.floor().get().pk).update(upkeep_retiring=True, is_pending_deletion=True)
        self.assertEqual(self.reconcile()['spawned'], 1)

    def test_carried_chest_is_replaced_and_new_contents_use_the_replacement(self):
        _, chest = self.add_chest()
        original_contents = set(chest.inventory.values_list('pk', flat=True))
        move_items([chest], self.player)
        self.assertEqual(self.reconcile()['spawned'], 3)
        replacement = Item.objects.filter(world=self.spawn_world, definition=chest.definition).exclude(pk=chest.pk).get()
        self.assertEqual(replacement.container, self.room)
        self.assertEqual(replacement.inventory.count(), 2)
        move_items(list(chest.inventory.all()), self.player)
        self.assertEqual(self.reconcile()['spawned'], 0)
        self.assertEqual(chest.inventory.count(), 0)
        self.assertEqual(self.player.inventory.filter(pk__in=original_contents).count(), 2)

    def test_returned_chest_satisfies_added_parent_slot_and_children_use_both(self):
        parent, chest = self.add_chest()
        move_items([chest], self.player)
        self.reconcile()
        parent.count = 2
        parent.save(update_fields=['count'])
        move_items([chest], self.room)
        self.assertEqual(self.reconcile()['spawned'], 0)
        boxes = Item.objects.filter(world=self.spawn_world, definition=chest.definition)
        self.assertEqual(boxes.count(), 2)
        self.assertEqual([box.inventory.count() for box in boxes], [2, 2])

    def test_bundle_counts_materializations_and_preserves_partial_loot_semantics(self):
        self.floor().delete()
        bundle = ItemBundle.objects.create(world=self.world, slug='pair')
        ItemBundleEntry.objects.create(bundle=bundle, item_definition=self.definition, min_quantity=2, max_quantity=2)
        self.entry.source = 'itembundle.pair'
        self.entry.save(update_fields=['source'])
        self.assertEqual(self.reconcile()['spawned'], 2)
        first, second = list(self.floor())
        move_items([first], self.player)
        self.assertEqual(self.reconcile()['spawned'], 0)
        move_items([second], self.player)
        self.assertEqual(self.reconcile()['spawned'], 2)
        move_items([first, second], self.room)
        self.entry.count = 2
        self.entry.save(update_fields=['count'])
        self.assertEqual(self.reconcile()['spawned'], 0)
        self.assertEqual(self.floor().count(), 4)

    def test_healthy_count_queries_do_not_grow_per_slot(self):
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(self.reconcile()['spawned'], 0)
        self.entry.count = 64
        self.entry.save(update_fields=['count'])
        self.reconcile()
        with CaptureQueriesContext(connection) as large:
            self.assertEqual(self.reconcile()['spawned'], 0)
        self.assertLessEqual(len(large), len(small) + 1)
        print('Item stock healthy reconciliation queries:', len(small), len(large))

    def test_local_count_uses_container_index_with_ten_thousand_owned_copies(self):
        original = self.floor().get()
        run_id = original.spawn_placement.run_id
        Item.objects.bulk_create([Item(world=self.spawn_world, container=self.player,
            spawn_placement_id=original.spawn_placement_id, name='an owned copy') for _ in range(10000)], batch_size=1000)
        with connection.cursor() as cursor:
            cursor.execute('ANALYZE spawns_item')
        with CaptureQueriesContext(connection) as queries:
            stock = ItemStock(world_id=self.spawn_world.pk, run_id=run_id,
                              entry_slug=self.entry.slug, targets=[self.room])
        self.assertEqual(len(queries), 1)
        self.assertEqual(sum(sum(counts.values()) for counts in stock.counts.values()), 1)
        with connection.cursor() as cursor:
            cursor.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + queries[0]['sql'])
            plan = cursor.fetchone()[0]
        self.assertIn('spawn_item_live_container_def', json.dumps(plan))
        print('Item stock count profile:', {'owned_copies': 10000, 'queries': len(queries),
              'index': 'spawn_item_live_container_def', 'execution_ms': plan[0]['Execution Time']})


class SpawnItemStockConcurrencyTests(StockFixture, APITransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('stock-race@example.com', 'p')
        self.world = World.objects.new_world(name='Stock races', author=self.user)
        self.spawn_world = self.world.create_spawn_world()
        self.zone = self.world.zones.first()
        self.room = self.zone.rooms.first()
        self.player = Player.objects.create(world=self.spawn_world, room=self.room, user=self.user, name='Collector')
        self.setup_stock()

    @staticmethod
    def isolated(function):
        try:
            return function()
        finally:
            connections.close_all()

    def test_drop_holds_destination_and_poll_defers_without_consuming_deadline(self):
        original = self.floor().get()
        move_items([original], self.player)
        run = SpawnPlanRun.objects.get(plan=self.plan, spawn_world=self.spawn_world)
        previous = run.last_reconciled_at
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                move_items([original], self.room)
                result = pool.submit(self.isolated, self.reconcile).result(timeout=10)
                self.assertTrue(result['busy'])
                self.assertEqual(result['spawned'], 0)
        run.refresh_from_db()
        self.assertEqual(run.last_reconciled_at, previous)
        self.assertEqual(self.reconcile()['spawned'], 0)
        self.assertEqual(self.floor().count(), 1)

    def test_picked_up_parent_lock_never_deadlocks_or_refills_carried_chest(self):
        _, chest = self.add_chest()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                move_items([chest], self.player)
                result = pool.submit(self.isolated, self.reconcile).result(timeout=10)
                self.assertTrue(result['busy'])
                self.assertEqual(result['spawned'], 0)
        self.assertEqual(self.reconcile()['spawned'], 3)
        self.assertEqual(chest.inventory.count(), 2)

    def test_spawn_wins_then_return_waits_and_leaves_only_expected_surplus(self):
        from spawns.spawn_plans import _materialize_placement
        original = self.floor().get()
        move_items([original], self.player)
        counted, release, returning = Event(), Event(), Event()

        def hold(**kwargs):
            counted.set()
            if not release.wait(10):
                raise RuntimeError('Test failed to release spawn')
            return _materialize_placement(**kwargs)

        def drop():
            returning.set()
            move_items([original], self.room)

        with ThreadPoolExecutor(max_workers=2) as pool, patch('spawns.spawn_plans._materialize_placement', side_effect=hold):
            spawning = pool.submit(self.isolated, self.reconcile)
            try:
                self.assertTrue(counted.wait(10))
                dropping = pool.submit(self.isolated, drop)
                self.assertTrue(returning.wait(10))
                self.assertFalse(dropping.done())
            finally:
                release.set()
            self.assertEqual(spawning.result(timeout=10)['spawned'], 1)
            dropping.result(timeout=10)
        self.assertEqual(self.floor().count(), 2)
        self.assertEqual(self.reconcile()['spawned'], 0)

    def test_busy_hot_edit_retries_even_when_ordinary_respawn_is_disabled(self):
        from spawns.item_stock import lock_destinations
        self.plan.respawn_policy = {'mode': 'none'}
        self.plan.save(update_fields=['respawn_policy'])
        self.reconcile()
        self.entry.count = 2
        self.entry.save(update_fields=['count'])
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                lock_destinations(self.spawn_world.pk, [self.room])
                result = pool.submit(self.isolated, self.reconcile).result(timeout=10)
                self.assertTrue(result['busy'])
                self.assertEqual(result['spawned'], 0)
        self.assertEqual(self.reconcile()['spawned'], 1)
        self.assertEqual(self.floor().count(), 2)

    def test_destination_lock_does_not_block_other_runtime_of_same_room(self):
        from spawns.item_stock import lock_destinations
        second = self.world.create_spawn_world()
        second.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                lock_destinations(self.spawn_world.pk, [self.room])
                result = pool.submit(self.isolated, lambda: run_spawn_plans(world=second, initial=True)[0]).result(timeout=10)
                self.assertEqual(result['spawned'], 1)
                self.assertFalse(result['busy'])
