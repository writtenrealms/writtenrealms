from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import yaml
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection, connections
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.reverse import reverse

from builders.manifests import item_definition_to_manifest
from builders.models import ItemBundle, ItemBundleEntry, ItemDefinition
from core.item_storage import DEFAULT_CONTAINER_CAPACITY, MAX_CONTAINER_CAPACITY, MAX_TRANSFER_ITEMS
from spawns.actions.base import ActionError
from spawns.actions.items import PutAction
from spawns.item_lifecycle import move_items
from spawns.models import Item, Player
from tests.base import WorldTestCase
from worlds.models import World


class ContainerCapacityTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.apply_ep = reverse('builder-world-manifest-apply', args=[self.world.pk])
        self.bag = self.item(self.player, type='container', name='a bag', capacity=2)

    def item(self, target=None, **kwargs):
        return Item.objects.create(world=self.spawn_world, container=target or self.player,
                                   name=kwargs.pop('name', 'a stone'), **kwargs)

    def apply(self, **spec):
        return self.client.post(self.apply_ep, {'manifest': yaml.safe_dump({
            'kind': 'itemdefinition', 'metadata': {'slug': 'bag', 'name': 'a bag'},
            'spec': {'type': 'container', **spec},
        })}, format='json')

    def test_definition_default_export_and_spawn(self):
        response = self.apply()
        self.assertEqual(response.status_code, 201, response.data)
        definition = ItemDefinition.objects.get(world=self.world, slug='bag')
        self.assertEqual(definition.base_properties['capacity'], DEFAULT_CONTAINER_CAPACITY)
        self.assertEqual(definition.spawn(self.player, self.spawn_world).capacity, DEFAULT_CONTAINER_CAPACITY)
        self.assertEqual(item_definition_to_manifest(definition)['spec']['capacity'], DEFAULT_CONTAINER_CAPACITY)

    def test_definition_capacity_round_trip_and_update(self):
        response = self.apply(capacity=3)
        self.assertEqual(response.status_code, 201, response.data)
        definition = ItemDefinition.objects.get(world=self.world, slug='bag')
        bag = definition.spawn(self.player, self.spawn_world)
        self.assertEqual(bag.capacity, 3)
        manifest = item_definition_to_manifest(definition)
        self.assertEqual(manifest['spec']['capacity'], 3)
        response = self.client.post(self.apply_ep, {'manifest': yaml.safe_dump(manifest)}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.apply(description='A sturdy bag.')
        bag.refresh_from_db()
        self.assertEqual(bag.capacity, 3)
        response = self.apply(capacity=1)
        self.assertEqual(response.status_code, 200, response.data)
        bag.refresh_from_db()
        self.assertEqual(bag.capacity, 1)

    def test_definition_capacity_rejects_invalid_values(self):
        for capacity in (0, -1, MAX_CONTAINER_CAPACITY + 1, True, False, 1.5, 2.0, '2', None, [], {}):
            with self.subTest(capacity=capacity):
                response = self.apply(capacity=capacity)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('spec.capacity', str(response.data))
        self.assertFalse(ItemDefinition.objects.filter(world=self.world, slug='bag').exists())

    def test_definition_accepts_capacity_boundaries(self):
        for capacity in (1, MAX_CONTAINER_CAPACITY):
            response = self.apply(capacity=capacity)
            self.assertIn(response.status_code, (200, 201), response.data)

    def test_capacity_edits_do_not_fail_on_items_awaiting_cleanup(self):
        self.apply(capacity=3)
        definition = ItemDefinition.objects.get(world=self.world, slug='bag')
        bag = definition.spawn(self.room, self.spawn_world)
        Item.objects.filter(pk=bag.pk).update(is_pending_deletion=True, upkeep_retiring=True)
        response = self.apply(capacity=2)
        self.assertEqual(response.status_code, 200, response.data)
        bag.refresh_from_db()
        self.assertEqual(bag.capacity, 2)

    def test_put_exact_capacity_then_rejects_overflow(self):
        stones = [self.item() for _ in range(3)]
        PutAction().execute(self.player.pk, stones[0].key, 'bag', build_events=False)
        PutAction().execute(self.player.pk, stones[1].key, 'bag', build_events=False)
        with self.assertRaises(ActionError) as caught:
            PutAction().execute(self.player.pk, stones[2].key, 'bag', build_events=False)
        self.assertEqual(caught.exception.code, 'container_full')
        self.assertEqual(self.bag.inventory.count(), 2)
        stones[2].refresh_from_db()
        self.assertEqual(stones[2].container, self.player)

    def test_bulk_put_overflow_is_atomic(self):
        stones = [self.item() for _ in range(3)]
        with self.assertRaises(ActionError):
            PutAction().execute(self.player.pk, 'all.stone', 'bag', build_events=False)
        self.assertEqual(self.bag.inventory.count(), 0)
        self.assertEqual(self.player.inventory.filter(pk__in=[item.pk for item in stones]).count(), 3)

    def test_stacked_copies_each_consume_capacity(self):
        definition = ItemDefinition.objects.create(world=self.world, name='a coin', slug='coin')
        coins = [definition.spawn(self.player, self.spawn_world) for _ in range(3)]
        move_items(coins[:2], self.bag)
        with self.assertRaises(ActionError):
            move_items(coins[2:], self.bag)

    def test_spawn_and_model_save_cannot_bypass_capacity(self):
        self.item(self.bag)
        self.item(self.bag)
        definition = ItemDefinition.objects.create(world=self.world, name='a coin', slug='coin')
        with self.assertRaises(ActionError):
            definition.spawn(self.bag, self.spawn_world)
        stone = self.item()
        stone.container = self.bag
        with self.assertRaises(ActionError):
            stone.save(update_fields=['container_type', 'container_id'])
        stone.refresh_from_db()
        self.assertEqual(stone.container, self.player)
        self.assertEqual(self.bag.inventory.count(), 2)

    def test_bundle_that_does_not_fit_leaves_no_partial_spawn(self):
        definition = ItemDefinition.objects.create(world=self.world, name='a coin', slug='coin')
        bundle = ItemBundle.objects.create(world=self.world, name='coins', slug='coins')
        ItemBundleEntry.objects.create(bundle=bundle, item_definition=definition,
                                       min_quantity=3, max_quantity=3)
        with self.assertRaises(ActionError):
            bundle.spawn(self.bag, self.spawn_world)
        self.assertFalse(self.bag.inventory.exists())

    def test_new_item_with_explicit_id_still_consumes_capacity(self):
        self.item(self.bag)
        self.item(self.bag)
        with self.assertRaises(ActionError):
            self.item(self.bag, id=self.bag.pk + 100000)
        self.assertEqual(self.bag.inventory.count(), 2)

    def test_full_bag_can_be_moved_and_emptied(self):
        contents = [self.item(self.bag) for _ in range(2)]
        move_items([self.bag], self.room)
        move_items([self.bag], self.player)
        move_items(contents[:1], self.player)
        self.item(self.bag)
        self.assertEqual(self.bag.inventory.count(), 2)

    def test_no_op_move_does_not_double_count_contents(self):
        contents = [self.item(self.bag) for _ in range(2)]
        move_items(contents, self.bag)
        self.assertEqual(self.bag.inventory.count(), 2)

    def test_containers_cannot_be_put_or_spawned_inside_bags(self):
        for item_type in ('container', 'trash', 'corpse'):
            with self.subTest(item_type=item_type):
                inner = self.item(type=item_type)
                with self.assertRaises(ActionError) as caught:
                    move_items([inner], self.bag)
                self.assertEqual(caught.exception.code, 'container_nested')
                with self.assertRaises(ActionError):
                    self.item(self.bag, type=item_type)
        self.assertEqual(self.bag.inventory.count(), 0)

    def test_mixed_bulk_deposit_with_a_bag_is_atomic(self):
        stone = self.item()
        inner = self.item(type='container')
        with self.assertRaises(ActionError):
            move_items([stone, inner], self.bag)
        self.assertEqual(self.bag.inventory.count(), 0)

    def test_changing_a_resident_into_a_container_is_rejected(self):
        stone = self.item(self.bag)
        stone.type = 'container'
        with self.assertRaises(ActionError):
            stone.save(update_fields=['type'])
        stone.refresh_from_db()
        self.assertEqual(stone.type, 'inert')

    def test_definition_type_edit_cannot_create_nested_bags(self):
        self.apply(type='inert')
        definition = ItemDefinition.objects.get(world=self.world, slug='bag')
        item = definition.spawn(self.bag, self.spawn_world)
        response = self.apply(type='container')
        self.assertEqual(response.status_code, 400, response.data)
        definition.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(definition.item_type, 'inert')
        self.assertEqual(item.type, 'inert')

    def test_changing_type_during_move_cannot_bypass_nesting_rule(self):
        stone = self.item()
        stone.type = 'container'
        stone.container = self.bag
        with self.assertRaises(ActionError):
            stone.save()
        stone.refresh_from_db()
        self.assertEqual(stone.container, self.player)

    def test_corpses_can_retain_full_bags(self):
        corpse = self.item(self.room, type='corpse', is_pickable=False)
        contents = [self.item(self.bag) for _ in range(2)]
        move_items([self.bag], corpse)
        move_items([self.bag], self.player)
        self.assertEqual(self.bag.inventory.count(), len(contents))

    def test_existing_nested_bags_must_be_unnested_before_depositing(self):
        inner = self.item(type='container')
        Item.objects.filter(pk=inner.pk).update(container_type=ContentType.objects.get_for_model(Item),
                                               container_id=self.bag.pk)
        inner.refresh_from_db()
        for target in (self.bag, inner):
            with self.assertRaises(ActionError) as caught:
                move_items([self.item()], target)
            self.assertEqual(caught.exception.code, 'container_nested')
        move_items([inner], self.player)
        self.item(self.bag)

    def test_hidden_contents_still_consume_movement_budget(self):
        self.item(self.bag, is_pending_deletion=True)
        self.item(self.bag, upkeep_retiring=True)
        with self.assertRaises(ActionError):
            self.item(self.bag)

    def test_lowering_capacity_preserves_contents_and_allows_recovery(self):
        contents = [self.item(self.bag) for _ in range(2)]
        self.bag.capacity = 1
        self.bag.save(update_fields=['capacity'])
        with self.assertRaises(ActionError):
            self.item(self.bag)
        move_items([self.bag], self.room)
        move_items(contents, self.player)
        self.item(self.bag)

    def test_unconfigured_existing_bags_use_default_capacity(self):
        self.bag.capacity = 0
        self.bag.save(update_fields=['capacity'])
        self.fill(self.bag, DEFAULT_CONTAINER_CAPACITY)
        with self.assertRaises(ActionError):
            self.item(self.bag)

    def fill(self, bag, count):
        # Fast fixture creation for the actual upper-bound transfer test.
        Item.objects.bulk_create([Item(world=self.spawn_world, container=bag, upkeep_root=bag,
                                       name='a stone') for _ in range(count)], batch_size=1000)

    def test_largest_full_bag_moves_within_transfer_budget(self):
        self.bag.capacity = MAX_CONTAINER_CAPACITY
        self.bag.save(update_fields=['capacity'])
        self.fill(self.bag, MAX_CONTAINER_CAPACITY - 1)
        self.item(self.bag)
        self.assertEqual(self.bag.inventory.count() + 1, MAX_TRANSFER_ITEMS)
        with self.assertRaises(ActionError):
            self.item(self.bag)
        move_items([self.bag], self.room)
        move_items([self.bag], self.player)
        self.assertEqual(self.bag.inventory.count(), MAX_CONTAINER_CAPACITY)

    def test_runtime_capacity_cannot_exceed_transfer_ceiling(self):
        self.bag.capacity = MAX_TRANSFER_ITEMS * 2
        self.bag.save(update_fields=['capacity'])
        self.fill(self.bag, MAX_CONTAINER_CAPACITY)
        with self.assertRaises(ActionError):
            move_items([self.item()], self.bag)

    def test_capacity_check_uses_one_aggregate_for_bulk_deposits(self):
        self.bag.capacity = 100
        self.bag.save(update_fields=['capacity'])
        counts = []
        for size in (1, 64):
            items = [self.item() for _ in range(size)]
            with CaptureQueriesContext(connection) as queries:
                move_items(items, self.bag)
            aggregates = [query['sql'] for query in queries if 'COUNT(' in query['sql']]
            self.assertEqual(len(aggregates), 1)
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])


class ContainerCapacityConcurrencyTests(TransactionTestCase):
    def test_concurrent_deposits_cannot_claim_the_same_last_slot(self):
        user = get_user_model().objects.create_user('bag-races@example.com', 'p')
        world = World.objects.new_world(name='Container races', author=user)
        runtime = world.create_spawn_world()
        room = world.zones.first().rooms.first()
        bag = Item.objects.create(world=runtime, container=room, type='container', name='a bag', capacity=1)
        players = [Player.objects.create(world=runtime, room=room, user=user, name=f'Player {i}')
                   for i in range(2)]
        stones = [Item.objects.create(world=runtime, container=player, name='a stone') for player in players]
        ready = Barrier(2)

        def deposit(index):
            try:
                ready.wait(timeout=10)
                try:
                    PutAction().execute(players[index].pk, stones[index].key, 'bag', build_events=False)
                    return 'stored'
                except ActionError as error:
                    return error.code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(deposit, range(2)))
        self.assertCountEqual(outcomes, ['stored', 'container_full'])
        self.assertEqual(bag.inventory.count(), 1)
        self.assertEqual(sum(player.inventory.count() for player in players), 1)
