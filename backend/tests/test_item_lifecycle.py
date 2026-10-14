from datetime import timedelta
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from config import constants
from spawns.actions.base import ActionError
from spawns.actions.items import GetAction, DropAction, PutAction
from spawns.item_lifecycle import (arm_corpse, authored_placement, move_items,
                                  process_due_items, refresh_empty_corpses)
from spawns.models import Item, GameEventOutbox
from tests.base import WorldTestCase
from worlds.upkeep_policy import normalize_upkeep, normalize_timer, effective_upkeep


class ItemLifecycleTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.spawn_world.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
        self.player.in_game = True
        self.player.save(update_fields=['in_game'])

    def item(self, target=None, **kwargs):
        return Item.objects.create(world=self.spawn_world, container=target or self.room,
                                   name=kwargs.pop('name', 'a stone'), **kwargs)

    def corpse(self, kind='npc', contents=True):
        corpse = self.item(type='corpse', name='the corpse of a wolf', is_pickable=False)
        loot = self.item(corpse) if contents else None
        arm_corpse(corpse, kind=kind, owner_id=self.player.pk if kind == 'player' else None)
        corpse.refresh_from_db()
        return corpse, loot

    def test_corpse_starts_at_death_and_player_empty_default(self):
        corpse = self.item(type='corpse')
        self.assertIsNone(corpse.upkeep_due_ts)
        Item.objects.filter(pk=corpse.pk).update(created_ts=timezone.now()-timedelta(days=2))
        loot = self.item(corpse)
        now = timezone.now()
        with patch('spawns.item_lifecycle.gameplay_now', return_value=now):
            arm_corpse(corpse, kind='player', owner_id=self.player.pk)
        corpse.refresh_from_db()
        self.assertIsNone(corpse.upkeep_due_ts)
        with patch('spawns.item_lifecycle.gameplay_now', return_value=now):
            move_items([loot], self.player)
        corpse.refresh_from_db()
        self.assertEqual(corpse.upkeep_due_ts, now + timedelta(minutes=10))
        self.assertEqual(corpse.upkeep_owner_id, self.player.pk)

    def test_npc_lifetime_and_empty_shortening(self):
        corpse, loot = self.corpse()
        self.assertEqual(corpse.upkeep_due_ts - corpse.upkeep_started_ts, timedelta(minutes=10))
        move_items([loot], self.player)
        corpse.refresh_from_db()
        self.assertEqual(corpse.upkeep_due_ts - corpse.upkeep_empty_ts, timedelta(seconds=60))

    def test_loot_feedback_uses_the_new_empty_deadline(self):
        corpse, loot = self.corpse('player')
        result = GetAction().execute(self.player.pk, 'all', 'corpse')
        notice = result.events[0].data['source']['upkeep_notice']
        self.assertIn('10 minutes', notice)
        self.assertNotIn('Retained while', notice)

    def test_npc_empty_never_extends_death_deadline(self):
        corpse, loot = self.corpse()
        deadline = corpse.upkeep_due_ts
        with patch('spawns.item_lifecycle.gameplay_now', return_value=deadline-timedelta(seconds=10)):
            move_items([loot], self.player)
        corpse.refresh_from_db()
        self.assertEqual(corpse.upkeep_due_ts, deadline)

    def test_nonpickable_contents_prevent_emptying(self):
        corpse, loot = self.corpse('player')
        self.item(corpse, is_pickable=False)
        move_items([loot], self.player)
        corpse.refresh_from_db()
        self.assertIsNone(corpse.upkeep_empty_ts)
        self.assertIsNone(corpse.upkeep_due_ts)

    def test_refilling_empty_player_corpse_does_not_extend_it(self):
        corpse, _ = self.corpse('player', contents=False)
        deadline = corpse.upkeep_due_ts
        loot = self.item(self.player)
        move_items([loot], corpse)
        corpse.refresh_from_db()
        self.assertEqual(corpse.upkeep_due_ts, deadline)
        with patch('spawns.events.flush_game_event_outbox'):
            process_due_items(self.spawn_world.pk, now=deadline+timedelta(seconds=1))
            process_due_items(self.spawn_world.pk, now=deadline+timedelta(seconds=1))
        self.assertFalse(Item.objects.filter(pk=loot.pk).exists())

    def test_original_player_equipment_has_no_ground_timer(self):
        corpse, loot = self.corpse('player')
        loot.refresh_from_db()
        self.assertIsNone(loot.upkeep_due_ts)
        process_due_items(self.spawn_world.pk, now=timezone.now()+timedelta(days=366))
        self.assertTrue(Item.objects.filter(pk=corpse.pk).exists())
        self.assertTrue(Item.objects.filter(pk=loot.pk).exists())

    def test_drop_recover_and_drop_again(self):
        item = self.item(self.player)
        self.assertIsNone(item.upkeep_due_ts)
        DropAction().execute(self.player.pk, 'stone')
        item.refresh_from_db()
        self.assertEqual(item.upkeep_due_ts-item.upkeep_started_ts, timedelta(hours=1))
        first = item.upkeep_due_ts
        GetAction().execute(self.player.pk, 'stone')
        item.refresh_from_db()
        self.assertIsNone(item.upkeep_due_ts)
        with patch('spawns.item_lifecycle.gameplay_now', return_value=first):
            DropAction().execute(self.player.pk, 'stone')
        item.refresh_from_db()
        self.assertEqual(item.upkeep_due_ts, first + timedelta(hours=1))

    def test_nested_bag_recovery_cancels_all_ground_deadlines(self):
        bag = self.item(type='container', name='a bag')
        inner = self.item(bag, type='container')
        loot = self.item(inner)
        GetAction().execute(self.player.pk, 'bag')
        for obj in [bag, inner, loot]:
            obj.refresh_from_db()
            self.assertIsNone(obj.upkeep_due_ts)
            self.assertEqual(obj.upkeep_root_id, bag.pk)

    def test_placement_and_storage_are_distinct(self):
        with authored_placement({}):
            fixture = self.item(type='container', name='a chest', is_pickable=False)
        deposit = self.item(self.player)
        move_items([deposit], fixture)
        fixture.refresh_from_db(); deposit.refresh_from_db()
        self.assertIsNone(fixture.upkeep_due_ts)
        self.assertIsNotNone(deposit.upkeep_due_ts)
        with authored_placement({'contents': 'preserve'}):
            storage = self.item(type='container', is_pickable=False)
        move_items([deposit], storage)
        deposit.refresh_from_db()
        self.assertIsNone(deposit.upkeep_due_ts)

    def test_authored_portable_item_loses_placement_protection_when_acquired(self):
        with authored_placement({}):
            item = self.item()
        self.assertTrue(item.upkeep_placed)
        move_items([item], self.player)
        move_items([item], self.room)
        item.refresh_from_db()
        self.assertFalse(item.upkeep_placed)
        self.assertIsNotNone(item.upkeep_due_ts)

    def test_same_location_save_preserves_deadline_and_placement(self):
        with authored_placement({}):
            item = self.item()
        item.label = 'read me'
        item.save()
        item.refresh_from_db()
        self.assertTrue(item.upkeep_placed)
        self.assertIsNone(item.upkeep_due_ts)

    def test_episode_policy_survives_config_change(self):
        corpse, loot = self.corpse('player')
        self.world.config.upkeep = normalize_upkeep({'corpses': {'player': {'empty': {'mode':'fixed','seconds':3}}}})
        self.world.config.save(update_fields=['upkeep'])
        move_items([loot], self.player)
        corpse.refresh_from_db()
        self.assertEqual(corpse.upkeep_due_ts-corpse.upkeep_empty_ts, timedelta(minutes=10))
        newer, _ = self.corpse('player', contents=False)
        self.assertEqual(newer.upkeep_due_ts-newer.upkeep_empty_ts, timedelta(seconds=3))

    def test_zone_override_and_disabled_lifetime(self):
        self.zone.ground_item_upkeep = {'mode':'fixed','seconds':25}
        self.zone.save(update_fields=['ground_item_upkeep'])
        item = self.item()
        self.assertEqual(item.upkeep_due_ts-item.upkeep_started_ts, timedelta(seconds=25))
        self.zone.ground_item_upkeep = {'mode':'none'}
        self.zone.save(update_fields=['ground_item_upkeep'])
        self.assertIsNone(self.item().upkeep_due_ts)

    def test_cleanup_is_bounded_and_resumable(self):
        corpse, _ = self.corpse(contents=False)
        for _ in range(12):
            self.item(corpse)
        now = corpse.upkeep_due_ts + timedelta(seconds=1)
        for _ in range(20):
            result = process_due_items(self.spawn_world.pk, now=now, budget=3)
            self.assertLessEqual(result['work'], 3)
            if not Item.objects.filter(pk=corpse.pk).exists():
                break
        self.assertFalse(Item.objects.filter(upkeep_root_id=corpse.pk).exists())
        self.assertFalse(Item.objects.filter(pk=corpse.pk).exists())

    def test_recovered_item_and_other_runtime_survive(self):
        item = self.item()
        deadline = item.upkeep_due_ts
        move_items([item], self.player)
        second_world = self.world.create_spawn_world()
        other = Item.objects.create(world=second_world, container=self.room, name='other')
        process_due_items(self.spawn_world.pk, now=deadline+timedelta(days=1))
        self.assertEqual(Item.objects.filter(pk__in=[item.pk, other.pk]).count(), 2)

    def test_retiring_ancestor_blocks_put_and_get(self):
        bag = self.item(type='container')
        inner = self.item(bag, type='container')
        loot = self.item(inner)
        Item.objects.filter(pk=bag.pk).update(upkeep_retiring=True, is_pending_deletion=True)
        with self.assertRaises(ActionError):
            move_items([loot], self.player)
        with self.assertRaises(ActionError):
            move_items([self.item(self.player)], inner)

    def test_corpse_cannot_be_hidden_in_an_expiring_container(self):
        corpse, loot = self.corpse('player')
        bag = self.item(type='container')
        with self.assertRaises(ActionError):
            move_items([corpse], bag)
        move_items([corpse], self.room)
        loot.refresh_from_db()
        self.assertIsNone(loot.upkeep_due_ts)

    def test_abandoned_movement_preserves_earliest_deadline_and_nested_floor_removal(self):
        first = self.item(type='container')
        second = self.item(type='container')
        item = self.item(first)
        deadline = first.upkeep_due_ts
        move_items([item], second)
        move_items([item], self.room)
        item.refresh_from_db()
        self.assertEqual(item.upkeep_due_ts, deadline)

    def test_pickup_and_put_into_protected_storage_cancels_descendant_timers(self):
        bag = self.item(type='container')
        loot = self.item(bag)
        with authored_placement({'contents':'preserve'}):
            storage = self.item(type='container')
        move_items([bag], storage)
        for item in [bag, loot]:
            item.refresh_from_db()
            self.assertIsNone(item.upkeep_due_ts)

    def test_deposit_uses_an_inner_containers_earlier_deadline(self):
        outer = self.item(type='container')
        inner = self.item(outer, type='container')
        earlier = outer.upkeep_due_ts-timedelta(minutes=20)
        Item.objects.filter(pk=inner.pk).update(upkeep_due_ts=earlier)
        deposit = self.item(self.player)
        move_items([deposit], inner)
        deposit.refresh_from_db()
        self.assertEqual(deposit.upkeep_due_ts, earlier)

    def test_moving_distinct_abandonment_episodes_uses_one_bulk_write(self):
        items = [self.item() for _ in range(64)]
        deadlines = {item.pk: item.upkeep_due_ts for item in items}
        with CaptureQueriesContext(connection) as queries:
            move_items(items, self.room)
        writes = [q['sql'] for q in queries if q['sql'].startswith('UPDATE "spawns_item"')]
        self.assertEqual(len(writes), 1)
        self.assertEqual(dict(Item.objects.filter(pk__in=deadlines).values_list('pk','upkeep_due_ts')), deadlines)

    def test_death_with_a_bag_larger_than_the_interactive_transfer_limit(self):
        from spawns.actions.combat import _apply_player_death_penalty
        bag = self.item(self.player, type='container')
        Item.objects.bulk_create([Item(world=self.spawn_world, container=bag, upkeep_root=bag,
                                       name='a keepsake') for _ in range(5000)], batch_size=1000)
        _, corpse_id, _ = _apply_player_death_penalty(player=self.player,
            death_mode=constants.DEATH_MODE_LOSE_ALL, death_currency=None,
            death_currency_penalty=0, origin_room=self.room, is_pvp_death=False)
        corpse = Item.objects.get(pk=corpse_id)
        self.assertIsNone(corpse.upkeep_due_ts)
        self.assertEqual(Item.objects.filter(upkeep_root=corpse, upkeep_due_ts__isnull=True).count(), 5002)
        keepsake = bag.inventory.order_by('pk').first()
        move_items([keepsake], self.player)
        keepsake.refresh_from_db()
        self.assertIsNone(keepsake.upkeep_due_ts)
        self.assertEqual(keepsake.container, self.player)

    def test_npc_death_with_loot_larger_than_the_interactive_transfer_limit(self):
        mob = self.create_mob('a hoarder')
        corpse = mob.create_corpse()
        bag = self.item(mob, type='container')
        keepsake = self.item(bag)
        Item.objects.bulk_create([Item(world=self.spawn_world, container=corpse, upkeep_root=corpse,
                                       name='a coin') for _ in range(5000)], batch_size=1000)
        mob.delete()
        corpse.refresh_from_db()
        keepsake.refresh_from_db()
        self.assertEqual(corpse.container, self.room)
        self.assertEqual(corpse.upkeep_kind, 'npc_corpse')
        self.assertEqual(corpse.upkeep_due_ts-corpse.upkeep_started_ts, timedelta(minutes=10))
        self.assertEqual(Item.objects.filter(upkeep_root=corpse).count(), 5003)
        self.assertIsNone(keepsake.upkeep_due_ts)

    def test_retained_population_does_not_increase_cleanup_queries(self):
        item = self.item()
        # Warm content type caches and process a baseline due item.
        now = item.upkeep_due_ts + timedelta(seconds=1)
        with CaptureQueriesContext(connection) as small:
            process_due_items(self.spawn_world.pk, now=now)
        with authored_placement({}):
            for _ in range(100):
                self.item()
        self.item()
        with CaptureQueriesContext(connection) as large:
            process_due_items(self.spawn_world.pk, now=now+timedelta(minutes=1))
        self.assertLessEqual(len(large), len(small)+2)
