from datetime import timedelta
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from builders.world_export import serialize_world_export_payload, manifest_stream_to_yaml
from builders.instance_templates import create_instance_template
from config import constants
from spawns.instance_clock import simulation_scope
from spawns.instance_clock_transitions import _rebase_timers
from spawns.item_lifecycle import arm_corpse, move_items, process_due_items
from spawns.models import Item, GameEventOutbox
from spawns.tasks import dispatch_item_upkeep
from tests.base import WorldTestCase
from worlds.instances import create_fresh_instance_run
from worlds.upkeep_policy import effective_upkeep


class UpkeepIntegrationTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)
        self.spawn_world.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)

    def apply(self, docs):
        response = self.client.post(reverse('builder-world-manifest-apply', args=[self.world.pk]),
            {'manifest': manifest_stream_to_yaml(docs)}, format='json')
        self.assertIn(response.status_code, (200, 201), response.data)
        return response

    def test_world_and_zone_partial_patches_round_trip(self):
        from builders.currencies import create_currency
        self.spawn_world.set_lifecycle(constants.WORLD_LIFECYCLE_STOPPED)
        create_currency(world=self.world, code='silver', name='Silver')
        self.apply([{'kind':'world', 'spec':{'upkeep':{'corpses':{'player':{'empty':{'mode':'fixed','seconds':700}}}}}},
                    {'kind':'zone', 'metadata':{'ref':f'zone@{self.zone.relative_id}', 'name':self.zone.name},
                     'spec':{'ground_item_upkeep':{'mode':'fixed','seconds':120}}}])
        self.apply([{'kind':'world', 'spec':{'upkeep':{'ground_items':{'abandoned':{'mode':'none'}}}}}])
        self.world.refresh_from_db()
        policy = effective_upkeep(self.world)
        self.assertEqual(policy['corpses']['player']['empty']['seconds'], 700)
        self.assertEqual(policy['ground_items']['abandoned'], {'mode':'none'})
        docs = serialize_world_export_payload(self.world)['documents']
        self.assertEqual(next(d['spec']['upkeep'] for d in docs if d['kind']=='world'), policy)
        zone = next(d for d in docs if d['kind']=='zone')
        self.assertEqual(zone['spec']['ground_item_upkeep']['seconds'], 120)
        self.apply(docs)

    def test_spawn_entry_storage_round_trip_and_materialization(self):
        from builders.models import ItemDefinition, SpawnEntry
        from spawns.loading import run_spawn_plans_for_world
        ItemDefinition.objects.create(world=self.world, slug='storage-box', name='a box', item_type='container')
        self.apply([{'kind':'spawnplan', 'metadata':{'slug':'storage', 'name':'Storage'}, 'spec':{
            'zone': f'zone@{self.zone.relative_id}', 'entries':[{'slug':'box',
                'source':'itemdefinition.storage-box', 'target':f'room@{self.room.relative_id}',
                'count':1, 'upkeep':{'contents':'preserve'}}]}}])
        self.assertEqual(SpawnEntry.objects.get(plan__world=self.world).upkeep, {'contents':'preserve'})
        docs = serialize_world_export_payload(self.world)['documents']
        manifest = next(d for d in docs if d['kind']=='spawnplan')
        self.assertEqual(manifest['spec']['entries'][0]['upkeep'], {'contents':'preserve'})
        run_spawn_plans_for_world(world=self.spawn_world, initial=True)
        box = Item.objects.get(world=self.spawn_world, definition__slug='storage-box')
        self.assertTrue(box.upkeep_storage)
        deposit = Item.objects.create(world=self.spawn_world, container=box, name='deposit')
        self.assertIsNone(deposit.upkeep_due_ts)

    def test_concurrent_partial_policy_edits_preserve_omitted_fields(self):
        from builders.manifests import parse_world_config_manifest, apply_world_config_manifest
        first = parse_world_config_manifest(world=self.world, manifest={'kind':'world', 'spec':{
            'upkeep':{'corpses':{'npc':{'empty':{'mode':'none'}}}}}})
        second = parse_world_config_manifest(world=self.world, manifest={'kind':'world', 'spec':{
            'upkeep':{'ground_items':{'abandoned':{'mode':'none'}}}}})
        apply_world_config_manifest(first)
        apply_world_config_manifest(second)
        self.world.refresh_from_db()
        policy = effective_upkeep(self.world)
        self.assertEqual(policy['corpses']['npc']['empty']['mode'], 'none')
        self.assertEqual(policy['ground_items']['abandoned']['mode'], 'none')

    def make_run(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        template = create_instance_template(base_world=self.world, author=self.user,
                                             name='Upkeep room', instance_slug='upkeep-room')
        template.config.instance_single_player = True
        template.config.instance_time_control = True
        template.config.save(update_fields=['instance_single_player', 'instance_time_control'])
        zone = template.zones.first()
        zone.ground_item_upkeep = {'mode':'fixed','seconds':8}
        zone.save(update_fields=['ground_item_upkeep'])
        run = create_fresh_instance_run(template, leader=self.player)
        run.spawned_world.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
        return template, zone, run

    def test_instance_snapshots_world_and_zone_policy(self):
        template, zone, run = self.make_run()
        self.world.config.upkeep = {'corpses':{'player':{'empty':{'mode':'fixed','seconds':5}}}}
        self.world.config.save(update_fields=['upkeep'])
        zone.ground_item_upkeep = {'mode':'none'}
        zone.save(update_fields=['ground_item_upkeep'])
        self.assertEqual(effective_upkeep(run.spawned_world)['corpses']['player']['empty']['seconds'], 600)
        item = Item.objects.create(world=run.spawned_world, container=zone.rooms.first(), name='a stone')
        self.assertEqual(item.upkeep_due_ts-item.upkeep_started_ts, timedelta(seconds=8))

    def test_paused_world_excluded_and_simulation_can_expire_items(self):
        template, zone, run = self.make_run()
        item = Item.objects.create(world=run.spawned_world, container=zone.rooms.first(), name='a stone')
        run.time_paused = True
        run.simulation_time = item.upkeep_due_ts-timedelta(seconds=1)
        run.save(update_fields=['time_paused','simulation_time'])
        process_due_items(run.spawned_world_id, now=item.upkeep_due_ts+timedelta(days=1))
        self.assertTrue(Item.objects.filter(pk=item.pk, upkeep_retiring=False).exists())
        run.simulation_time = item.upkeep_due_ts+timedelta(seconds=1)
        with simulation_scope(run):
            process_due_items(run.spawned_world_id)
        self.assertFalse(Item.objects.filter(pk=item.pk).exists())

    def test_scheduler_rotates_runtimes_and_never_reload_does_not_disable_items(self):
        worlds = [self.spawn_world] + [self.world.create_spawn_world() for _ in range(2)]
        for world in worlds:
            world.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
            item = Item.objects.create(world=world, container=self.room, name='a stone')
            Item.objects.filter(pk=item.pk).update(upkeep_due_ts=timezone.now()-timedelta(seconds=1))
        self.world.config.never_reload = True
        self.world.config.save(update_fields=['never_reload'])
        with patch('config.game_settings.ITEM_UPKEEP_WORLD_BATCH', 1), patch('spawns.tasks.run_item_upkeep.apply_async') as enqueue:
            for _ in worlds:
                self.assertEqual(dispatch_item_upkeep(), 1)
            self.assertEqual({c.kwargs['args'][0] for c in enqueue.call_args_list}, {w.pk for w in worlds})
            self.assertTrue(all(c.kwargs['expires']==30 for c in enqueue.call_args_list))

    def test_room_events_are_bounded_and_preview_is_read_only(self):
        self.player.in_game = True
        self.player.save(update_fields=['in_game'])
        for _ in range(5):
            Item.objects.create(world=self.spawn_world, container=self.room, name='a stone')
        Item.objects.filter(world=self.spawn_world).update(upkeep_due_ts=timezone.now()-timedelta(seconds=1))
        output = StringIO()
        call_command('upkeep_preview', self.spawn_world.pk, due=True, limit=2, stdout=output)
        self.assertEqual(len(json.loads(output.getvalue())['items']), 2)
        self.assertEqual(Item.objects.filter(world=self.spawn_world).count(), 5)
        process_due_items(self.spawn_world.pk)
        events = GameEventOutbox.objects.filter(event_type='item.expired')
        self.assertEqual(events.count(), 1)
        process_due_items(self.spawn_world.pk)
        self.assertEqual(events.count(), 1)

    def test_disable_control_preserves_deadlines(self):
        item = Item.objects.create(world=self.spawn_world, container=self.room, name='a stone')
        with patch('config.game_settings.ITEM_UPKEEP_ENABLED', False):
            process_due_items(self.spawn_world.pk, now=item.upkeep_due_ts+timedelta(days=1))
        self.assertTrue(Item.objects.filter(pk=item.pk, upkeep_due_ts=item.upkeep_due_ts).exists())

    def test_door_dispatch_is_independent_bounded_and_rotates(self):
        from worlds.tasks import run_world_door_upkeep
        second = self.world.create_spawn_world()
        second.set_lifecycle(constants.WORLD_LIFECYCLE_RUNNING)
        with patch('config.game_settings.DOOR_UPKEEP_WORLD_BATCH', 1), patch('worlds.tasks.run_runtime_door_upkeep.apply_async') as enqueue:
            self.assertEqual(run_world_door_upkeep(), 1)
            self.assertEqual(run_world_door_upkeep(), 1)
            self.assertEqual({c.kwargs['args'][0] for c in enqueue.call_args_list}, {self.spawn_world.pk, second.pk})

    def test_door_zone_budget_defers_without_starvation(self):
        from spawns.door_upkeep import process_due_door_resets
        from worlds.models import ZoneDoorResetSchedule
        other = self.world.zones.create(name='Other zone')
        process_due_door_resets(world=self.spawn_world, initial=True)
        past = timezone.now()-timedelta(minutes=1)
        ZoneDoorResetSchedule.objects.filter(world=self.spawn_world).update(next_reset_ts=past)
        process_due_door_resets(world=self.spawn_world, budget=1)
        self.assertEqual(ZoneDoorResetSchedule.objects.filter(world=self.spawn_world, next_reset_ts=past).count(), 1)
        process_due_door_resets(world=self.spawn_world, budget=1)
        self.assertFalse(ZoneDoorResetSchedule.objects.filter(world=self.spawn_world, next_reset_ts=past).exists())

    def test_resume_rebases_gameplay_anchors_but_not_audit_timestamps(self):
        template, zone, run = self.make_run()
        item = Item.objects.create(world=run.spawned_world, container=zone.rooms.first(), name='a stone')
        deadline, started, created = item.upkeep_due_ts, item.upkeep_started_ts, item.created_ts
        _rebase_timers(run, timedelta(hours=2))
        item.refresh_from_db()
        self.assertEqual(item.upkeep_due_ts, deadline+timedelta(hours=2))
        self.assertEqual(item.upkeep_started_ts, started+timedelta(hours=2))
        self.assertEqual(item.created_ts, created)
