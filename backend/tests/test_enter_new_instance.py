from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from builders.models import MobDefinition, SpawnEntry, SpawnPlan, SpawnPlanRun
from config import constants
from config.exceptions import ServiceError
from core.scoped_state import STATE_SCOPE_WORLD, get_state_snapshot, replace_initial_state_snapshot
from quests.models import QuestInstance, QuestOfferState, QuestTemplate
from spawns.models import GameEventOutbox, InstanceClockWork, Item, Mob, Player
from tests.utils import capture_game_messages, dispatch_text_command
from worlds import instances
from worlds.models import InstanceClearRecord, InstanceParticipant, InstanceRun, World, WorldConfig
from worlds.services import WorldSmith


class InstanceEntryFixture:
    def setUp(self):
        super().setUp()
        self.enterContext(patch('spawns.tasks.reconcile_combat_room.apply_async'))
        self.enterContext(patch('spawns.tasks.resume_instance_schedulers.delay'))
        self.enterContext(patch('spawns.events.publish_to_player'))
        self.user = get_user_model().objects.create_user('enter-new@example.com', 'p')
        self.base = World.objects.new_world(
            name='Base', author=self.user, config=WorldConfig.objects.create(),
            is_multiplayer=True,
        )
        self.base_runtime = self.base.create_spawn_world()
        self.entrance = self.base.config.starting_room
        self.template = World.objects.new_world(
            name='Adventure', author=self.user, config=WorldConfig.objects.create(),
            instance_of=self.base, is_multiplayer=True,
        )
        self.entry = self.template.config.starting_room
        self.entrance.transfer_to = self.entry
        self.entrance.save(update_fields=['transfer_to'])
        self.player = self.make_player('Owner')

    def make_player(self, name):
        return Player.objects.create(
            name=name, user=self.user, world=self.base_runtime,
            room=self.entrance, in_game=True,
        )

    def enter(self, player=None, *, fresh=False, ref=None):
        return World.enter_instance(
            player=player or self.player, transfer_to_id=self.entry.pk,
            transfer_from_id=self.entrance.pk, fresh=fresh, ref=ref,
        )

    def leave(self, player=None):
        player = player or self.player
        instances.leave_instance(player=player)
        player.refresh_from_db()

    def empty_run(self):
        world = self.enter()
        self.leave()
        return world.instance_run

    def dispatch(self, text):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.pk, text)
        return [entry['message'] for entry in messages]


class TestEnterNewInstance(InstanceEntryFixture, TestCase):
    def test_command_creates_first_run_and_ordinary_enter_resumes_it(self):
        messages = self.dispatch('enter new')
        self.player.refresh_from_db()
        run = self.player.world.instance_run
        self.assertEqual(run.leader_id, self.player.pk)
        announcement = GameEventOutbox.objects.get(event_type='cmd.enter.success', data__world_id=run.spawned_world_id)
        self.assertEqual(announcement.text, f'New Instance ID: {run.ref}')
        self.assertTrue(any(m['type'] == 'cmd.state.sync.success' for m in messages))
        self.leave()
        self.assertEqual(self.enter().pk, run.spawned_world_id)

    def test_replacement_refreshes_population_state_and_quests_preserving_character_and_records(self):
        definition = MobDefinition.objects.create(
            world=self.base, slug='guard', name='Guard',
            mob_type=constants.MOB_TYPE_CONSTRUCT, base_properties={'health_max': 10},
        )
        plan = SpawnPlan.objects.create(
            world=self.template, zone=self.entry.zone, slug='guards', name='Guards',
            respawn_policy={'mode': 'none'},
        )
        SpawnEntry.objects.create(
            plan=plan, slug='guard', source='mobdefinition.guard', target_room=self.entry, count=1,
        )
        world = self.enter()
        run = world.instance_run
        old_mob = Mob.objects.get(world=world)
        ground = Item.objects.create(world=world, container=self.entry, name='Rock')
        bag = Item.objects.create(world=world, container=self.player, name='Bag', type=constants.ITEM_TYPE_CONTAINER)
        gem = Item.objects.create(world=world, container=bag, name='Gem')
        sword = Item.objects.create(world=world, container=self.player, name='Sword')
        self.player.equipment.weapon = sword
        self.player.equipment.save(update_fields=['weapon'])
        quest = QuestTemplate.objects.create(world=self.template, slug='task', name='Task')
        attempt = QuestInstance.objects.create(world=world, player=self.player, template=quest)
        offer = QuestOfferState.objects.create(
            world=world, player=self.player, template=quest,
            cooldown_until=timezone.now() + timezone.timedelta(days=1),
        )
        work = InstanceClockWork.objects.create(world=world, kind='command', payload={})
        base_quest = QuestTemplate.objects.create(world=self.base, slug='lasting', name='Lasting')
        base_attempt = QuestInstance.objects.create(world=self.base_runtime, player=self.player, template=base_quest)
        record = InstanceClearRecord.objects.create(
            run=run, template_world=self.template, template_name=self.template.name,
            template_slug=self.template.instance_slug, attempt_id=uuid4(),
            started_at=timezone.now(), completed_at=timezone.now(), clear_time_ms=1000,
        )
        run.status = InstanceRun.STATUS_COMPLETED
        run.save(update_fields=['status'])
        self.leave()
        replace_initial_state_snapshot(STATE_SCOPE_WORLD, self.template, {'phase': 'fresh'})
        messages = self.dispatch('enter new')
        self.player.refresh_from_db()
        new = self.player.world.instance_run
        self.assertNotEqual(new.ref, run.ref)
        announcement = GameEventOutbox.objects.get(event_type='cmd.enter.success', data__world_id=new.spawned_world_id)
        self.assertEqual(announcement.text, f'New Instance ID: {new.ref}')
        self.assertEqual(new.status, InstanceRun.STATUS_ACTIVE)
        self.assertEqual(self.player.room_id, self.entry.pk)
        self.assertFalse(InstanceRun.objects.filter(pk=run.pk).exists())
        self.assertFalse(World.objects.filter(pk=world.pk).exists())
        for obj in (old_mob, ground, attempt, offer, work):
            self.assertFalse(type(obj).objects.filter(pk=obj.pk).exists())
        self.assertEqual(Mob.objects.filter(world_id=new.spawned_world_id, definition=definition).count(), 1)
        self.assertEqual(get_state_snapshot(STATE_SCOPE_WORLD, self.player.world), {'phase': 'fresh'})
        for item in (bag, gem, sword):
            item.refresh_from_db()
            self.assertEqual(item.world_id, new.spawned_world_id)
        self.player.equipment.refresh_from_db()
        self.assertEqual(self.player.equipment.weapon_id, sword.pk)
        self.assertTrue(QuestInstance.objects.filter(pk=base_attempt.pk).exists())
        record.refresh_from_db()
        self.assertIsNone(record.run_id)
        self.assertEqual(record.clear_time_ms, 1000)
        self.assertTrue(any(m['type'] == 'cmd.state.sync.success' for m in messages))

    def test_online_and_offline_occupants_block_even_without_active_participation(self):
        run = self.empty_run()
        member = self.make_player('Member')
        self.enter(member, ref=run.ref)
        # Occupancy must use actual location, not only participation bookkeeping.
        InstanceParticipant.objects.filter(run=run, player=member).update(
            exited_at=timezone.now(), exit_reason=InstanceParticipant.EXIT_REASON_LEFT,
            return_runtime_world=None,
        )
        for online in (True, False):
            with self.subTest(online=online):
                Player.objects.filter(pk=member.pk).update(in_game=online)
                messages = self.dispatch('enter new')
                error = next(m for m in messages if m['type'] == 'cmd.enter.error')
                self.assertIn('Everyone must leave', error['text'])
                self.assertEqual(InstanceRun.objects.count(), 1)
                self.player.refresh_from_db()
                member.refresh_from_db()
                self.assertEqual(self.player.world_id, self.base_runtime.pk)
                self.assertEqual(member.world_id, run.spawned_world_id)

    def test_members_who_left_do_not_block(self):
        run = self.empty_run()
        member = self.make_player('Member')
        self.enter(member, ref=run.ref)
        self.leave(member)
        self.assertNotEqual(self.enter(fresh=True).pk, run.spawned_world_id)
        member.refresh_from_db()
        self.assertEqual(member.world_id, self.base_runtime.pk)

    def test_joining_another_leaders_run_does_not_give_reset_authority(self):
        leader = self.make_player('Other Leader')
        other = self.enter(leader).instance_run
        self.enter(ref=other.ref)
        self.leave()
        new = self.enter(fresh=True).instance_run
        self.assertEqual(new.leader_id, self.player.pk)
        self.assertTrue(InstanceRun.objects.filter(pk=other.pk).exists())
        leader.refresh_from_db()
        self.assertEqual(leader.world_id, other.spawned_world_id)

    def test_replaces_only_callers_run_for_this_template(self):
        mine = self.empty_run()
        other_player = self.make_player('Other')
        other = self.enter(other_player).instance_run
        self.leave(other_player)
        new = self.enter(fresh=True)
        self.assertNotEqual(new.pk, mine.spawned_world_id)
        self.assertTrue(InstanceRun.objects.filter(pk=other.pk).exists())

    def test_single_player_ownership_survives_leader_change(self):
        config = self.template.config
        config.instance_single_player = True
        config.instance_time_control = True
        config.save(update_fields=['instance_single_player', 'instance_time_control'])
        run = self.empty_run()
        InstanceRun.objects.filter(pk=run.pk).update(leader=self.make_player('New Leader'), time_paused=True)
        new = self.enter(fresh=True).instance_run
        self.assertEqual(new.owner_id, self.player.pk)
        self.assertTrue(new.time_control)
        self.assertFalse(InstanceRun.objects.filter(pk=run.pk).exists())

    def test_cannot_replace_from_inside_or_combine_new_with_reference(self):
        run = self.enter().instance_run
        with self.assertRaisesRegex(RuntimeError, 'Use leave first'):
            self.enter(fresh=True)
        messages = self.dispatch('enter new')
        self.assertTrue(any('Use leave first' in m.get('text', '') for m in messages))
        self.leave()
        with self.assertRaisesRegex(RuntimeError, 'without an instance reference'):
            self.enter(fresh=True, ref=run.ref)
        self.assertTrue(InstanceRun.objects.filter(pk=run.pk).exists())

    def test_requires_linked_entrance_in_service_and_command(self):
        run = self.empty_run()
        self.entrance.transfer_to = None
        self.entrance.save(update_fields=['transfer_to'])
        with self.assertRaisesRegex(RuntimeError, 'no entrance'):
            self.enter(fresh=True)
        messages = self.dispatch('enter new')
        self.assertTrue(any('no instance entrance' in m.get('text', '') for m in messages))
        self.assertTrue(InstanceRun.objects.filter(pk=run.pk).exists())

    def test_match_template_cannot_bypass_duel_admission(self):
        self.template.config.pvp_mode = constants.PVP_MODE_MATCH
        self.template.config.save(update_fields=['pvp_mode'])
        messages = self.dispatch('enter new')
        self.assertTrue(any('accepted duel' in m.get('text', '') for m in messages))
        self.assertFalse(InstanceRun.objects.exists())

    def test_startup_failure_preserves_previous_run_and_reports_error(self):
        run = self.empty_run()
        with patch('worlds.instances._ensure_spawned_instance_started', side_effect=ServiceError('failed')):
            messages = self.dispatch('enter new')
        self.assertTrue(any('previous instance has been kept' in m.get('text', '') for m in messages))
        self.assertEqual(list(InstanceRun.objects.values_list('pk', flat=True)), [run.pk])
        self.assertEqual(self.enter().pk, run.spawned_world_id)

    def test_cleanup_failure_restores_deleted_runtime_and_keeps_player_outside(self):
        run = self.empty_run()
        item = Item.objects.create(world=self.base_runtime, container=self.player, name='Keepsake')
        old_item = Item.objects.create(world=run.spawned_world, container=self.entry, name='Old rock')
        old_outbox = set(GameEventOutbox.objects.values_list('pk', flat=True))
        stop = WorldSmith.stop

        def failing_stop(smith, **kwargs):
            stop(smith, **kwargs)
            raise ServiceError('cleanup failed after deletion')

        with patch.object(WorldSmith, 'stop', failing_stop):
            with self.assertRaises(ServiceError):
                self.enter(fresh=True)
        self.player.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.player.world_id, self.base_runtime.pk)
        self.assertEqual(item.world_id, self.base_runtime.pk)
        self.assertTrue(Item.objects.filter(pk=old_item.pk).exists())
        self.assertEqual(list(InstanceRun.objects.values_list('pk', flat=True)), [run.pk])
        self.assertEqual(set(GameEventOutbox.objects.values_list('pk', flat=True)), old_outbox)
        self.assertEqual(self.enter().pk, run.spawned_world_id)

    def test_goal_failure_after_cleanup_rolls_back_entry_inventory_and_events(self):
        run = self.empty_run()
        item = Item.objects.create(world=self.base_runtime, container=self.player, name='Keepsake')
        old_item = Item.objects.create(world=run.spawned_world, container=self.entry, name='Old rock')
        old_outbox = set(GameEventOutbox.objects.values_list('pk', flat=True))
        self.template.config.instance_goal = {'type': 'clear_initial_mobs', 'where': {'always': True}}
        self.template.config.save(update_fields=['instance_goal'])
        messages = self.dispatch('enter new')
        self.assertTrue(any('previous instance has been kept' in m.get('text', '') for m in messages))
        self.player.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.player.world_id, self.base_runtime.pk)
        self.assertEqual(item.world_id, self.base_runtime.pk)
        self.assertTrue(Item.objects.filter(pk=old_item.pk).exists())
        self.assertEqual(list(InstanceRun.objects.values_list('pk', flat=True)), [run.pk])
        self.assertEqual(set(GameEventOutbox.objects.values_list('pk', flat=True)), old_outbox)
        self.assertEqual(self.enter().pk, run.spawned_world_id)

    def test_stale_entry_location_rolls_back_new_run(self):
        run = self.empty_run()
        other_room = self.entrance.create_at(constants.DIRECTION_NORTH)
        start = instances._ensure_spawned_instance_started

        def move_before_entry(new):
            start(new)
            Player.objects.filter(pk=self.player.pk).update(room=other_room)

        with patch('worlds.instances._ensure_spawned_instance_started', side_effect=move_before_entry):
            with self.assertRaisesRegex(RuntimeError, 'moved away'):
                self.enter(fresh=True)
        self.assertEqual(list(InstanceRun.objects.values_list('pk', flat=True)), [run.pk])

    def test_occupancy_check_is_one_bounded_query_for_many_characters(self):
        run = self.empty_run()
        for i in range(25):
            player = self.make_player(f'Occupant {i}')
            Player.objects.filter(pk=player.pk).update(world_id=run.spawned_world_id, room=self.entry)
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesRegex(RuntimeError, 'Everyone must leave'):
                self.enter(fresh=True)
        occupant_queries = [q['sql'] for q in queries if 'FROM "spawns_player"' in q['sql']]
        self.assertEqual(len(occupant_queries), 2)  # Caller origin and occupancy.
        self.assertIn('SELECT 1 AS', occupant_queries[-1])
        self.assertIn('LIMIT 1', occupant_queries[-1])

    def test_help_and_entrance_info_describe_fresh_entry(self):
        for command in ('help enter', 'instance'):
            with self.subTest(command=command):
                messages = self.dispatch(command)
                self.assertIn('enter new', str(messages))


@skipUnless(connection.vendor == 'postgresql', 'Instance entry row-lock tests require PostgreSQL.')
class TestEnterNewInstanceConcurrency(InstanceEntryFixture, TransactionTestCase):
    def worker_enter(self, player_id, ready, *, fresh=False, ref=None):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '8s'")
                cursor.execute("SET statement_timeout = '15s'")
                cursor.execute('SELECT pg_backend_pid()')
                ready.put(cursor.fetchone()[0])
            player = Player.objects.get(pk=player_id)
            try:
                return ('entered', self.enter(player, fresh=fresh, ref=ref).pk)
            except RuntimeError as exc:
                return ('error', str(exc))
        finally:
            connection.close()

    def wait_blocked(self, pid):
        deadline = monotonic() + 5
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute('SELECT cardinality(pg_blocking_pids(%s)) > 0', [pid])
                if cursor.fetchone()[0]:
                    return
            sleep(0.01)
        self.fail(f'Connection {pid} did not reach a lock wait.')

    def test_same_runtime_population_initialization_remains_serialized(self):
        from spawns.spawn_plans import reconcile_spawn_plan

        run = instances.create_fresh_instance_run(self.template, leader=self.player)
        definition = MobDefinition.objects.create(
            world=self.base, slug='guard', name='Guard',
            mob_type=constants.MOB_TYPE_CONSTRUCT, base_properties={'health_max': 10},
        )
        plan = SpawnPlan.objects.create(
            world=self.template, zone=self.entry.zone, slug='guards', name='Guards',
            respawn_policy={'mode': 'none'},
        )
        SpawnEntry.objects.create(
            plan=plan, slug='guard', source='mobdefinition.guard', target_room=self.entry, count=1,
        )
        ready = Queue()

        def reconcile():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '8s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    ready.put(cursor.fetchone()[0])
                reconcile_spawn_plan(spawn_world=run.spawned_world, plan=plan, initial=True)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                reconcile_spawn_plan(spawn_world=run.spawned_world, plan=plan, initial=True)
                pending = executor.submit(reconcile)
                self.wait_blocked(ready.get(timeout=5))
            pending.result(timeout=15)
        self.assertEqual(SpawnPlanRun.objects.filter(spawn_world=run.spawned_world, plan=plan).count(), 1)
        self.assertEqual(Mob.objects.filter(world=run.spawned_world, definition=definition).count(), 1)

    def test_simultaneous_first_entries_create_only_one_run(self):
        for modes in ((True, True), (True, False)):
            with self.subTest(modes=modes):
                player = self.make_player(f'Concurrent {modes}')
                ready = Queue()
                with ThreadPoolExecutor(max_workers=2) as executor:
                    with transaction.atomic():
                        instances._lock_player_instance(self.template.pk, player.pk)
                        futures = [executor.submit(self.worker_enter, player.pk, ready, fresh=fresh) for fresh in modes]
                        for _ in futures:
                            self.wait_blocked(ready.get(timeout=5))
                    outcomes = [future.result(timeout=15) for future in futures]
                self.assertEqual(sum(result[0] == 'entered' for result in outcomes), 1)
                self.assertEqual(InstanceRun.objects.filter(leader=player).count(), 1)

    def test_member_who_enters_first_blocks_replacement(self):
        run = self.empty_run()
        member = self.make_player('Joining Member')
        ready = Queue()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                InstanceRun.objects.select_for_update().get(pk=run.pk)
                pending = executor.submit(self.worker_enter, self.player.pk, ready, fresh=True)
                self.wait_blocked(ready.get(timeout=5))
                self.enter(member, ref=run.ref)
            outcome = pending.result(timeout=15)
        self.assertEqual(outcome[0], 'error')
        self.assertIn('Everyone must leave', outcome[1])
        member.refresh_from_db()
        self.assertEqual(member.world_id, run.spawned_world_id)
        self.assertEqual(InstanceRun.objects.count(), 1)

    def test_replacement_fences_reference_entry_without_blocking_other_owners(self):
        definition = MobDefinition.objects.create(
            world=self.base, slug='concurrent-guard', name='Guard',
            mob_type=constants.MOB_TYPE_CONSTRUCT, base_properties={'health_max': 10},
        )
        plan = SpawnPlan.objects.create(
            world=self.template, zone=self.entry.zone, slug='guards', name='Guards',
            respawn_policy={'mode': 'none'},
        )
        SpawnEntry.objects.create(
            plan=plan, slug='guard', source='mobdefinition.concurrent-guard',
            target_room=self.entry, count=1,
        )
        run = self.empty_run()
        member = self.make_player('Late Member')
        other = self.make_player('Unrelated Owner')
        paused, release = Event(), Event()
        stop = WorldSmith.stop

        def pause_cleanup(smith, **kwargs):
            if smith.world.pk == run.spawned_world_id:
                paused.set()
                if not release.wait(timeout=10):
                    raise RuntimeError('Test did not release replacement')
            return stop(smith, **kwargs)

        with patch.object(WorldSmith, 'stop', pause_cleanup):
            with ThreadPoolExecutor(max_workers=3) as executor:
                replacing = executor.submit(self.worker_enter, self.player.pk, Queue(), fresh=True)
                try:
                    self.assertTrue(paused.wait(timeout=5))
                    ready = Queue()
                    joining = executor.submit(self.worker_enter, member.pk, ready, ref=run.ref)
                    self.wait_blocked(ready.get(timeout=5))
                    unrelated = executor.submit(self.worker_enter, other.pk, Queue())
                    try:
                        self.assertEqual(unrelated.result(timeout=5)[0], 'entered')
                    except TimeoutError:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pid, query, pg_blocking_pids(pid) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'")
                            self.fail(f'Unrelated entry blocked: {cursor.fetchall()}')
                    self.assertFalse(replacing.done())
                finally:
                    release.set()
                self.assertEqual(replacing.result(timeout=15)[0], 'entered')
                self.assertEqual(joining.result(timeout=15)[0], 'error')
        member.refresh_from_db()
        self.assertEqual(member.world_id, self.base_runtime.pk)
        self.assertFalse(InstanceRun.objects.filter(pk=run.pk).exists())
        self.assertEqual(Mob.objects.filter(definition=definition).count(), 2)
