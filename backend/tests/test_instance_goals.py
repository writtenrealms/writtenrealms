from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from builders.models import Faction, MobDefinition, SpawnPlan, SpawnEntry
from builders.manifests import parse_world_config_manifest, apply_world_config_manifest, world_config_to_manifest
from builders.serializers import WorldConfigSerializer
from spawns.events import flush_game_event_outbox
from spawns.models import GameEventOutbox, Mob
from tests.base import WorldTestCase
from worlds.instance_goals import (
    DEFEAT_EVENT, normalize_instance_goal, process_instance_mob_defeat,
    record_instance_mob_defeat, start_instance_goal,
)
from worlds.instances import create_fresh_instance_run, enter_players_into_run, reset_instance
from worlds.models import World, WorldConfig, InstanceRun, InstanceClearRecord


PERSIAN_GOAL = {'type': 'clear_initial_mobs', 'where': {'eq': ['event.mob.core_faction', 'persian']}}


class TestInstanceGoals(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch('spawns.events.publish_to_player'))
        self.enterContext(patch('spawns.tasks.resume_instance_schedulers.delay'))
        self.enterContext(patch('spawns.tasks.reconcile_combat_room.apply_async'))
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=['is_multiplayer'])
        self.template = World.objects.new_world(
            name='A Persian Outpost', author=self.user, instance_of=self.world,
            is_multiplayer=True, config=WorldConfig.objects.create(instance_goal=deepcopy(PERSIAN_GOAL)),
        )
        self.entry = self.template.config.starting_room
        self.persian = Faction.objects.create(world=self.world, code='persian', name='Persian', type='core')
        self.greek = Faction.objects.create(world=self.world, code='greek', name='Greek', type='core')
        self.run = create_fresh_instance_run(self.template, leader=self.player)
        self.run.spawned_world.lifecycle = 'running'
        self.run.spawned_world.save(update_fields=['lifecycle'])

    def mob(self, name='Guard', faction=None, world=None):
        mob = Mob.objects.create(
            name=name, world=world or self.run.spawned_world, room=self.entry,
            health=10, health_max=10, definition_slug_snapshot='persian-guard',
        )
        mob.faction_assignments.create(faction=faction or self.persian)
        return mob

    def enter(self, players=None):
        self.run = enter_players_into_run(
            self.run, players_and_transfer_rooms=[(p, self.room) for p in (players or [self.player])],
            entry_room=self.entry,
        )
        self.player.refresh_from_db()
        # Entry events are unrelated to this focused completion test.
        GameEventOutbox.objects.exclude(event_type=DEFEAT_EVENT).delete()

    def defeat(self, mob, at=None):
        with transaction.atomic(), patch('worlds.instance_goals.timezone.now', return_value=at or timezone.now()):
            record_instance_mob_defeat(mob)
            mob.delete()

    def drain(self):
        flush_game_event_outbox()
        self.run.refresh_from_db()

    def test_faction_cohort_starts_on_entry_and_records_last_death_not_delivery(self):
        first, second = self.mob(), self.mob()
        ally = self.mob('Ally', faction=self.greek)
        other_run = create_fresh_instance_run(self.template, leader=self.player)
        outsider = self.mob(world=other_run.spawned_world)
        self.assertIsNone(self.run.started_at)
        start = timezone.now()
        with patch('worlds.instance_goals.timezone.now', return_value=start):
            self.enter()
        self.assertEqual(self.run.started_at, start)
        self.assertEqual(self.run.progress['total'], 2)
        self.defeat(outsider, start + timedelta(seconds=1))
        self.defeat(ally, start + timedelta(seconds=2))
        self.defeat(first, start + timedelta(seconds=10))
        self.drain()
        self.assertEqual(self.run.progress['remaining'], 1)
        self.assertEqual(self.run.status, 'active')
        self.defeat(second, start + timedelta(seconds=20, microseconds=123000))
        with patch('worlds.instance_goals.timezone.now', return_value=start + timedelta(hours=1)):
            self.drain()
        record = InstanceClearRecord.objects.get()
        self.assertEqual(record.started_at, start)
        self.assertEqual(record.completed_at, start + timedelta(seconds=20, microseconds=123000))
        self.assertEqual(record.clear_time_ms, 20123)
        self.assertEqual(record.participants[0]['player_id'], self.player.pk)
        self.assertEqual(self.run.completed_at, record.completed_at)
        self.assertEqual(self.run.status, 'completed')
        self.assertEqual(self.run.outcome['time_basis'], 'wall')

    def test_duplicate_and_out_of_order_death_events_are_idempotent(self):
        first, second = self.mob(), self.mob()
        ids = [first.pk, second.pk]
        self.enter()
        start = self.run.started_at
        self.defeat(first, start + timedelta(seconds=5))
        self.defeat(second, start + timedelta(seconds=8))
        for mob_id in [ids[1], ids[1], ids[0], ids[0], ids[1]]:
            process_instance_mob_defeat({'mob_id': mob_id, 'world_id': self.run.spawned_world_id})
        self.drain()
        self.assertEqual(InstanceClearRecord.objects.count(), 1)
        self.assertEqual(InstanceClearRecord.objects.get().clear_time_ms, 8000)
        self.assertEqual(self.run.progress['remaining'], 0)

    def test_empty_cohort_rejects_admission_without_timer_or_participation(self):
        self.mob(faction=self.greek)
        with self.assertRaisesMessage(ValueError, 'matches no living initial mobs'):
            self.enter()
        self.run.refresh_from_db()
        self.player.refresh_from_db()
        self.assertIsNone(self.run.started_at)
        self.assertFalse(self.run.participants.exists())
        self.assertEqual(self.player.world_id, self.spawn_world.pk)

    def test_reentry_keeps_original_start_and_cohort(self):
        first = self.mob()
        self.enter()
        start, attempt = self.run.started_at, self.run.progress['attempt_id']
        World.leave_instance(player=self.player)
        self.player.refresh_from_db()
        late = self.mob()
        self.enter()
        self.assertEqual(self.run.started_at, start)
        self.assertEqual(self.run.progress['attempt_id'], attempt)
        self.assertEqual(list(self.run.goal_members.values_list('mob_runtime_id', flat=True)), [first.pk])
        self.defeat(first)
        self.drain()
        self.assertTrue(Mob.objects.filter(pk=late.pk).exists())
        self.assertEqual(self.run.status, 'completed')

    def test_rollback_does_not_count_death_and_admin_deletion_does_not_complete(self):
        mob = self.mob()
        mob_id = mob.pk
        self.enter()
        with self.assertRaises(RuntimeError), transaction.atomic():
            record_instance_mob_defeat(mob)
            mob.delete()
            raise RuntimeError('rollback')
        self.assertTrue(Mob.objects.filter(pk=mob_id).exists())
        self.assertFalse(GameEventOutbox.objects.filter(event_type=DEFEAT_EVENT).exists())
        self.assertIsNone(self.run.goal_members.get().defeated_at)
        Mob.objects.filter(pk=mob_id).delete()
        self.drain()
        self.assertEqual(self.run.status, 'active')

    def test_records_survive_run_deletion(self):
        mob = self.mob()
        self.enter()
        self.defeat(mob)
        self.drain()
        self.run.delete()
        record = InstanceClearRecord.objects.get()
        self.assertIsNone(record.run_id)
        self.assertEqual(record.participants[0]['name'], self.player.name)

    def test_template_edits_do_not_change_existing_goal_snapshot(self):
        self.template.config.instance_goal = {}
        self.template.config.save(update_fields=['instance_goal'])
        self.mob()
        self.enter()
        self.assertEqual(self.run.goal_spec, PERSIAN_GOAL)
        self.assertEqual(self.run.progress['total'], 1)

    def test_time_control_completion_resumes_clock(self):
        mob = self.mob()
        self.enter()
        self.run.time_control = True
        self.run.single_player = True
        self.run.owner = self.player
        self.run.time_paused = True
        self.run.simulation_time = self.run.started_at
        self.run.save()
        self.defeat(mob, self.run.started_at + timedelta(seconds=5))
        self.drain()
        self.assertEqual(self.run.status, 'completed')
        self.assertFalse(self.run.time_paused)
        self.assertTrue(InstanceClearRecord.objects.get().time_control)

    def test_group_history_is_retained_in_record(self):
        mob = self.mob()
        member = self.create_player('Member')
        self.enter([self.player, member])
        self.defeat(mob)
        self.drain()
        self.assertEqual([p['player_id'] for p in InstanceClearRecord.objects.get().participants], [self.player.pk, member.pk])

    def test_cohort_queries_do_not_grow_per_mob(self):
        self.mob()
        with CaptureQueriesContext(connection) as one:
            start_instance_goal(self.run)
        self.run.goal_members.all().delete()
        self.run.progress = {}
        for _ in range(30):
            self.mob()
        with CaptureQueriesContext(connection) as many:
            start_instance_goal(self.run)
        self.assertEqual(len(one), len(many))
        self.assertLessEqual(len(many), 8)

    def test_defeat_uses_indexed_member_update_without_population_scan(self):
        mob = self.mob()
        self.enter()
        with CaptureQueriesContext(connection) as queries:
            record_instance_mob_defeat(mob)
        cohort_sql = [q['sql'] for q in queries if 'worlds_instancegoalmember' in q['sql']]
        self.assertEqual(len(cohort_sql), 1)
        self.assertIn('UPDATE', cohort_sql[0])
        self.assertIn('mob_runtime_id', cohort_sql[0])
        self.assertFalse(any('FROM "spawns_mob"' in q['sql'] for q in queries))

    def test_manifest_and_serializer_round_trip_and_validate(self):
        manifest = {'kind': 'world', 'metadata': {'world': f'world.{self.template.pk}'}, 'spec': {'instance_goal': PERSIAN_GOAL}}
        parsed = parse_world_config_manifest(manifest=manifest, world=self.template)
        apply_world_config_manifest(parsed)
        self.assertEqual(world_config_to_manifest(world=self.template)['spec']['instance_goal'], PERSIAN_GOAL)
        serializer = WorldConfigSerializer(self.template.config, data={'instance_goal': PERSIAN_GOAL}, partial=True, context={'world': self.template})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        invalid = WorldConfigSerializer(self.world.config, data={'instance_goal': PERSIAN_GOAL}, partial=True, context={'world': self.world})
        self.assertFalse(invalid.is_valid())
        for bad in [None, [], {'type': 'unknown', 'where': True}, {'type': 'clear_initial_mobs', 'where': {'eq': ['player.name', 'Joe']}}, {'type': 'clear_initial_mobs', 'where': {'in': ['event.mob.core_faction', 'persian']}}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_instance_goal(bad)

    def test_faction_membership_uses_shared_dsl(self):
        self.run.goal_spec = {'type': 'clear_initial_mobs', 'where': {'eq': ['event.mob.factions.persian', True]}}
        mob = self.mob()
        self.mob(faction=self.greek)
        start_instance_goal(self.run)
        self.assertEqual(list(self.run.goal_members.values_list('mob_runtime_id', flat=True)), [mob.pk])

    def test_reset_replaces_cohort_and_timer_and_ignores_old_events(self):
        from tests.utils import apply_basic_stat_system
        apply_basic_stat_system(self.world)
        definition = MobDefinition.objects.create(world=self.world, slug='guard', name='Guard', base_properties={'health_max': 10})
        definition.faction_assignments.create(faction=self.persian)
        plan = SpawnPlan.objects.create(world=self.template, zone=self.entry.zone, slug='guards', respawn_policy={'mode': 'none'})
        SpawnEntry.objects.create(plan=plan, slug='guard', source='mobdefinition.guard', target_room=self.entry, count=1)
        old = self.mob()
        old_id = old.pk
        self.enter()
        old_start = self.run.started_at
        self.defeat(old, old_start + timedelta(seconds=3))
        reset_instance(player=self.player)
        self.run.refresh_from_db()
        new = Mob.objects.get(world=self.run.spawned_world)
        self.assertNotEqual(new.pk, old_id)
        self.assertGreaterEqual(self.run.started_at, old_start)
        self.drain()
        self.assertEqual(self.run.progress['remaining'], 1)
        self.assertFalse(InstanceClearRecord.objects.exists())
        self.defeat(new)
        self.drain()
        first_record = InstanceClearRecord.objects.get()
        reset_instance(player=self.player)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, 'active')
        self.assertIsNone(self.run.completed_at)
        self.defeat(Mob.objects.get(world=self.run.spawned_world))
        self.drain()
        self.assertEqual(InstanceClearRecord.objects.count(), 2)
        self.assertNotEqual(str(first_record.attempt_id), self.run.progress['attempt_id'])

    def test_all_death_finalizers_record_goals(self):
        from spawns.actions.combat import _append_mob_defeat_events, _append_uncredited_mob_defeat_events
        from spawns.combat_rewards import defeat_mob
        from spawns.combat_encounters import locked_combat
        from tests.combat_fixtures import create_combat_encounter
        from tests.utils import apply_basic_stat_system
        apply_basic_stat_system(self.world)
        mobs = [self.mob() for _ in range(3)]
        self.enter()
        with transaction.atomic():
            _append_mob_defeat_events(player=self.player, target_mob=mobs[0], room=self.entry, events=[])
            _append_uncredited_mob_defeat_events(target_mob=mobs[1], room=self.entry, killer=None, events=[])
        encounter = create_combat_encounter(world=self.run.spawned_world, room=self.entry, player=self.player, mob=mobs[2])
        with locked_combat(encounter_ids=[encounter.pk]) as context:
            target = context.actors[mobs[2].key]
            defeat_mob(context, context.encounters[encounter.pk], context.participant(target.key), target, self.player)
        self.drain()
        self.assertEqual(self.run.progress['remaining'], 0)
        self.assertEqual(self.run.status, 'completed')

    def test_simulated_death_is_durable_after_turn_output(self):
        from spawns.instance_clock import simulation_scope
        from spawns.instance_time import _drain_reactions
        from spawns.events import enqueue_game_events
        mob = self.mob()
        self.enter()
        self.run.simulation_time = self.run.started_at
        with simulation_scope(self.run) as state:
            self.defeat(mob)
            _drain_reactions(state)
            state.persisting = True
            enqueue_game_events(state.output)
        self.drain()
        self.assertEqual(self.run.status, 'completed')

    def test_ordinary_instance_entrance_initializes_goal(self):
        target = self.mob()
        spawned = World.enter_instance(
            player=self.player, transfer_to_id=self.entry.pk,
            transfer_from_id=self.room.pk,
        )
        run = InstanceRun.objects.get(spawned_world=spawned)
        self.assertEqual(run.pk, self.run.pk)
        self.assertIsNotNone(run.started_at)
        self.assertEqual(list(run.goal_members.values_list('mob_runtime_id', flat=True)), [target.pk])

    def test_completed_goal_reentry_preserves_clear_record_and_empty_population(self):
        from spawns.state_payloads import build_state_sync, get_player_with_related, serialize_world
        from tests.utils import apply_basic_stat_system
        apply_basic_stat_system(self.world)
        self.template.config.instance_single_player = True
        self.template.config.save(update_fields=['instance_single_player'])
        self.run.single_player = True
        self.run.owner = self.player
        self.run.save(update_fields=['single_player', 'owner'])
        self.defeated_target = self.mob()
        self.enter()
        self.defeat(self.defeated_target)
        self.drain()
        record = InstanceClearRecord.objects.get()
        original_progress = deepcopy(self.run.progress)
        World.leave_instance(player=self.player)
        self.player.refresh_from_db()
        returned = World.enter_instance(player=self.player, transfer_to_id=self.entry.pk, transfer_from_id=self.room.pk)
        self.run.refresh_from_db()
        self.assertEqual(returned.pk, self.run.spawned_world_id)
        self.assertEqual(self.run.status, InstanceRun.STATUS_COMPLETED)
        self.assertEqual(self.run.started_at, record.started_at)
        self.assertEqual(self.run.completed_at, record.completed_at)
        self.assertEqual(self.run.progress, original_progress)
        self.assertEqual(InstanceClearRecord.objects.count(), 1)
        self.assertFalse(returned.mobs.exists())
        player = get_player_with_related(self.player.pk)
        with CaptureQueriesContext(connection) as queries:
            payload = serialize_world(player.world)
        run_reads = [q['sql'] for q in queries if 'worlds_instancerun' in q['sql']]
        self.assertEqual(len(run_reads), 1)
        self.assertNotIn('goal_spec', run_reads[0])
        self.assertEqual(payload['instance_status'], 'completed')
        self.assertEqual(build_state_sync(player).model_dump()['world']['instance_status'], 'completed')

    def test_previous_group_member_can_revisit_completed_run_by_reference(self):
        target = self.mob()
        member = self.create_player('Member')
        self.enter([self.player, member])
        self.defeat(target)
        self.drain()
        member.refresh_from_db()
        World.leave_instance(player=member)
        member.refresh_from_db()
        returned = World.enter_instance(player=member, transfer_to_id=self.entry.pk,
                                        transfer_from_id=self.room.pk, ref=self.run.ref)
        self.assertEqual(returned.pk, self.run.spawned_world_id)
        self.assertEqual(returned.instance_run.status, 'completed')

    def test_fresh_run_status_is_not_completed(self):
        from spawns.state_payloads import serialize_world
        self.assertEqual(serialize_world(self.run.spawned_world)['instance_status'], 'active')
        self.assertIsNone(serialize_world(self.spawn_world).get('instance_status'))
