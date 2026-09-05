from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from builders.models import Faction, MobDefinition
from core.combat_formulas import normalize_combat_system
from spawns.actions.base import ActionError
from spawns.combat_encounters import engage, transact, CombatPolicy
from spawns.combat_rounds import resolve, leave_participant
from spawns.combat_publication import project_snapshot
from spawns.models import CombatEncounter, CombatParticipant, CombatRewardReceipt, Mob
from tests.base import WorldTestCase
from tests.utils import apply_basic_stat_system


class MultiParticipantCombatTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        from config import constants
        self.spawn_world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        self.spawn_world.save(update_fields=['lifecycle'])
        apply_basic_stat_system(self.world)
        self.world.config.combat_resolution_interval = 1
        self.world.config.combat_system = normalize_combat_system({
            'variance': {'enabled': False, 'percent': 0},
            'profiles': {'basic_physical': {
                'power_scale': 1, 'use_weapon_damage': False, 'can_dodge': False,
                'can_crit': False, 'mitigation': {'armor': False, 'resilience': False},
                'minimum': 1,
            }},
        })
        self.world.config.save(update_fields=['combat_resolution_interval', 'combat_system'])
        self.player.in_game = True
        self.player.health = 1000
        self.player.save(update_fields=['in_game', 'health'])
        self.greek = Faction.objects.create(world=self.world, code='greek', name='Greek', type='core')
        self.persian = Faction.objects.create(world=self.world, code='persian', name='Persian', type='core')
        self.player.faction_assignments.create(faction=self.greek)

    def mob(self, name, faction=None, **kwargs):
        mob = self.create_mob(name, health=1000, health_max=1000, **kwargs)
        mob.faction_assignments.create(faction=faction or self.persian)
        return mob

    def test_assistance_manifest_round_trip_and_condition_validation(self):
        from builders.manifests import (apply_mob_definition_manifest,
            mob_definition_to_manifest, parse_mob_definition_manifest)
        from rest_framework.exceptions import ValidationError
        condition = {'eq': ['state.character.captive', False]}
        manifest = {'kind': 'mobdefinition', 'metadata': {'slug': 'commander', 'name': 'Commander'},
                    'spec': {'combat': {'assist': 'allies', 'engage_when': condition}}}
        definition = apply_mob_definition_manifest(parse_mob_definition_manifest(world=self.world, manifest=manifest))
        exported = mob_definition_to_manifest(definition)
        self.assertEqual(exported['spec']['combat']['assist'], 'allies')
        self.assertEqual(exported['spec']['combat']['engage_when'], condition)
        manifest['spec']['combat']['engage_when'] = {'eq': ['state.world.captive', False]}
        with self.assertRaises(ValidationError):
            parse_mob_definition_manifest(world=self.world, manifest=manifest)

    def test_reconciliation_does_not_readmit_existing_opponent_pairs(self):
        for index in range(8):
            engage(self.player, self.mob(f'guard {index}', aggression='normal'))
        with patch('spawns.combat_reconciliation.transact') as admission:
            self.drain_room()
        admission.assert_not_called()

    @override_settings(COMBAT_MAX_PARTICIPANTS=2)
    def test_capacity_release_reconsiders_previously_refused_actor(self):
        from spawns.combat_rounds import detach_actor
        guard, waiting = self.mob('guard', aggression='passive'), self.mob('waiting', aggression='normal')
        engage(self.player, guard)
        self.drain_room()
        self.assertFalse(CombatParticipant.objects.filter(mob=waiting, is_active=True).exists())
        def remove(ctx):
            actor = ctx.actors[guard.key]
            actor.health = 0
            actor.save(update_fields=['health'])
            detach_actor(ctx, guard.key, reason='defeated')
        transact(remove, keys=[guard.key])
        self.drain_room()
        self.assertTrue(CombatParticipant.objects.filter(mob=waiting, is_active=True).exists())

    def test_one_encounter_owns_several_mobs_and_each_gets_one_turn(self):
        mobs = [self.mob(f'guard {i}') for i in range(3)]
        encounter = engage(self.player, mobs[0])[0]
        for mob in mobs[1:]:
            self.assertEqual(engage(mob, self.player)[0].pk, encounter.pk)
        self.assertEqual(CombatEncounter.objects.count(), 1)
        self.assertEqual(CombatParticipant.objects.filter(is_active=True).count(), 4)
        result = resolve(encounter.pk, auto_advance=False)
        attacks = [e for e in result.events if e.type == 'notification.combat.attack']
        self.assertEqual(len(attacks), 4)
        self.assertEqual(len({e.data['actor']['key'] for e in attacks}), 4)
        encounter.refresh_from_db()
        self.assertEqual(encounter.round_number, 1)

    def test_duplicate_generation_cannot_advance_again(self):
        encounter = engage(self.player, self.mob('guard'))[0]
        generation = encounter.schedule_generation
        resolve(encounter.pk, auto_advance=False, expected_round=0, expected_generation=generation)
        result = resolve(encounter.pk, auto_advance=False, expected_round=0, expected_generation=generation)
        self.assertFalse(result.events)
        encounter.refresh_from_db()
        self.assertEqual(encounter.round_number, 1)

    def test_npc_only_round_and_player_exit_keep_remaining_fight(self):
        commander = self.mob('commander', self.greek)
        guard = self.mob('guard')
        encounter = engage(self.player, guard)[0]
        engage(commander, guard)
        transact(lambda ctx: leave_participant(ctx, ctx.participant(self.player.key), reason='fled'),
                 encounter_ids=[encounter.pk])
        result = resolve(encounter.pk, auto_advance=False)
        self.assertTrue(result.encounter_active)
        attacks = [e for e in result.events if e.type == 'notification.combat.attack']
        self.assertEqual(len(attacks), 2)
        self.assertEqual({e.data['actor']['key'] for e in attacks}, {commander.key, guard.key})

    def test_compatible_merge_preserves_members_and_invalidates_donor_schedule(self):
        other = self.create_player('ally')
        other.in_game = True
        other.health = 1000
        other.save(update_fields=['in_game', 'health'])
        other.faction_assignments.create(faction=self.greek)
        left, right = self.mob('left'), self.mob('right')
        first = engage(self.player, left)[0]
        second = engage(other, right)[0]
        merged = engage(self.player, right)[0]
        self.assertEqual(merged.pk, first.pk)
        self.assertEqual(CombatParticipant.objects.filter(encounter=merged, is_active=True).count(), 4)
        second.refresh_from_db()
        self.assertEqual(second.merged_into_id, first.pk)
        self.assertFalse(resolve(second.pk, auto_advance=False).events)

    @override_settings(COMBAT_MAX_PARTICIPANTS=2)
    def test_capacity_rejects_before_mutation(self):
        guard, extra = self.mob('guard'), self.mob('extra')
        encounter = engage(self.player, guard)[0]
        before = (encounter.state_revision, encounter.schedule_generation)
        with self.assertRaisesMessage(ActionError, 'participant limit'):
            engage(extra, self.player)
        encounter.refresh_from_db()
        self.assertEqual((encounter.state_revision, encounter.schedule_generation), before)
        self.assertFalse(CombatParticipant.objects.filter(mob=extra).exists())

    def test_third_side_is_rejected_without_changing_existing_fight(self):
        guard = self.mob('guard')
        third = self.create_mob('neutral', health=1000)
        encounter = engage(self.player, guard)[0]
        with self.assertRaisesMessage(ActionError, 'another combat side'):
            engage(third, self.player)
        self.assertEqual(encounter.participants.count(), 2)

    def test_unattended_encounter_pauses_without_due_job(self):
        self.player.in_game = False
        self.player.save(update_fields=['in_game'])
        encounter = engage(self.mob('commander', self.greek), self.mob('guard'))[0]
        CombatEncounter.objects.filter(pk=encounter.pk).update(npc_active_until=timezone.now() - timedelta(seconds=1))
        resolve(encounter.pk, auto_advance=False)
        encounter.refresh_from_db()
        self.assertEqual(encounter.status, CombatEncounter.STATUS_PAUSED)
        self.assertIsNone(encounter.next_resolution_ts)
        self.assertEqual(encounter.participants.filter(is_active=True).count(), 2)

    def test_join_preserves_due_round_and_existing_initiative(self):
        guard = self.mob('guard')
        encounter = engage(self.player, guard)[0]
        before = (encounter.schedule_generation, encounter.next_resolution_ts)
        initiatives = dict(encounter.participants.values_list('pk', 'initiative'))
        engage(self.mob('reinforcement'), self.player)
        encounter.refresh_from_db()
        self.assertEqual((encounter.schedule_generation, encounter.next_resolution_ts), before)
        self.assertEqual(dict(encounter.participants.filter(pk__in=initiatives).values_list('pk', 'initiative')), initiatives)

    def test_policy_checks_do_not_query_per_candidate(self):
        guard = self.mob('guard')
        def check(ctx):
            actor, target = ctx.actors[self.player.key], ctx.actors[guard.key]
            policy = CombatPolicy(list(ctx.actors.values()))
            with self.assertNumQueries(0):
                for _ in range(100):
                    self.assertEqual(policy.relationship(actor, target), 'hostile')
                    self.assertTrue(policy.automatic_allowed(target, actor))
        transact(check, keys=[self.player.key, guard.key])

    def test_merge_above_capacity_leaves_both_fights_unchanged(self):
        ally = self.create_player('ally')
        ally.health = 1000
        ally.save(update_fields=['health'])
        ally.faction_assignments.create(faction=self.greek)
        left, right = self.mob('left'), self.mob('right')
        first, second = engage(self.player, left)[0], engage(ally, right)[0]
        with override_settings(COMBAT_MAX_PARTICIPANTS=3):
            with self.assertRaises(ActionError):
                engage(self.player, right)
        self.assertEqual(CombatEncounter.objects.filter(status='active').count(), 2)
        self.assertEqual(first.participants.filter(is_active=True).count(), 2)
        self.assertEqual(second.participants.filter(is_active=True).count(), 2)

    def test_freed_commander_forms_npc_fight_without_player_present(self):
        from spawns.models import MobState
        from spawns.combat_reconciliation import request_reconciliation
        definition = MobDefinition.objects.create(world=self.world, name='Commander',
            combat_engage_when={'eq': ['state.character.captive', False]})
        commander = self.mob('commander', self.greek, definition=definition, aggression='normal')
        state = MobState.objects.create(mob=commander, data={'captive': True})
        self.player.in_game = False
        self.player.save(update_fields=['in_game'])
        request_reconciliation(self.spawn_world.pk, self.room.pk, observed=True)
        self.drain_room()
        self.assertFalse(CombatParticipant.objects.filter(mob=commander, is_active=True).exists())
        state.data = {'captive': False}
        state.save(update_fields=['data'])
        headsman = self.mob('headsman', aggression='passive')
        self.drain_room()
        encounter = CombatParticipant.objects.get(mob=commander, is_active=True).encounter
        self.assertTrue(encounter.participants.filter(mob=headsman, is_active=True).exists())
        self.assertFalse(encounter.participants.filter(player__isnull=False).exists())

    def test_observer_reactivates_paused_npc_fight_with_new_revision(self):
        self.player.in_game = False
        self.player.save(update_fields=['in_game'])
        encounter = engage(self.mob('commander', self.greek), self.mob('guard'))[0]
        CombatEncounter.objects.filter(pk=encounter.pk).update(npc_active_until=timezone.now() - timedelta(seconds=1))
        result = resolve(encounter.pk, auto_advance=False)
        paused = next(e for e in result.events if e.type == 'notification.combat.snapshot')
        self.player.in_game = True
        self.player.save(update_fields=['in_game'])
        self.drain_room()
        encounter.refresh_from_db()
        self.assertEqual(encounter.status, 'active')
        self.assertGreater(encounter.state_revision, paused.data['state_revision'])
        self.assertIsNotNone(encounter.next_resolution_ts)

    def test_projection_removes_hidden_actors_and_references(self):
        snapshot = {'participants': [
            {'key': self.player.key, 'side': 1, 'current_target': 'mob.7', 'effects': []},
            {'key': 'mob.7', 'side': 2, '_concealed': True, 'effects': []},
        ]}
        projected = project_snapshot(snapshot, self.player.key)
        self.assertEqual(len(projected['participants']), 1)
        self.assertIsNone(projected['participants'][0]['current_target'])
        self.assertEqual(len(snapshot['participants']), 2)

    def drain_room(self):
        from spawns.combat_reconciliation import reconcile
        from spawns.models import CombatRoomState
        for _ in range(100):
            state = CombatRoomState.objects.filter(next_run_ts__isnull=False).first()
            if state is None:
                return
            reconcile(state.pk)
        self.fail('Room reconciliation did not converge within its bounded pages')

    def test_freed_commander_and_arriving_allies_join_the_same_fight(self):
        from spawns.models import MobState
        definition = MobDefinition.objects.create(world=self.world, name='Commander', combat_assist='allies',
            combat_engage_when={'eq': ['state.character.captive', False]})
        commander = self.mob('commander', self.greek, definition=definition, attackable=False)
        state = MobState.objects.create(mob=commander, data={'captive': True})
        guard = self.mob('headsman')
        encounter = engage(self.player, guard)[0]
        self.drain_room()
        self.assertFalse(CombatParticipant.objects.filter(mob=commander, is_active=True).exists())
        commander.attackable = True
        commander.save(update_fields=['attackable'])
        state.data = {'captive': False}
        state.save(update_fields=['data'])
        self.drain_room()
        self.assertEqual(CombatParticipant.objects.get(mob=commander, is_active=True).encounter_id, encounter.pk)
        newcomer = self.mob('reinforcement', aggression='normal')
        self.drain_room()
        self.assertEqual(CombatParticipant.objects.get(mob=newcomer, is_active=True).encounter_id, encounter.pk)
        self.assertEqual(encounter.participants.filter(is_active=True).count(), 4)

    def test_assistance_is_opt_in_and_cohort_scoped(self):
        from spawns.combat_encounters import engage_locked
        guard = self.mob('guard')
        ally = self.mob('ally', self.greek, aggression='passive')
        encounter = engage(self.player, guard)[0]
        self.drain_room()
        self.assertFalse(CombatParticipant.objects.filter(mob=ally, is_active=True).exists())
        definition = MobDefinition.objects.create(world=self.world, name='Ally', combat_assist='same_spawn_cohort')
        ally.definition = definition
        ally.save(update_fields=['definition'])
        with self.assertRaises(ActionError):
            transact(lambda ctx: engage_locked(ctx, ctx.actors[ally.key], ctx.actors[guard.key],
                reason='assist', ally_key=self.player.key), keys=[ally.key, guard.key, self.player.key])
        self.assertEqual(encounter.participants.filter(is_active=True).count(), 2)

    def test_automatic_conditions_are_rechecked_under_admission_locks(self):
        from spawns.combat_encounters import engage_locked
        from spawns.models import MobState
        definition = MobDefinition.objects.create(world=self.world, name='Guard',
            combat_engage_when={'eq': ['state.character.free', True]})
        guard = self.mob('guard', definition=definition)
        MobState.objects.create(mob=guard, data={'free': False})
        with self.assertRaises(ActionError) as raised:
            transact(lambda ctx: engage_locked(ctx, ctx.actors[guard.key], ctx.actors[self.player.key],
                reason='automatic'), keys=[guard.key, self.player.key])
        self.assertEqual(raised.exception.code, 'engagement_ineligible')
        self.assertFalse(CombatParticipant.objects.filter(is_active=True).exists())

    def test_manual_commands_wait_for_both_players_once_each(self):
        from spawns.combat_rounds import resolve_locked
        other = self.create_player('ally')
        other.in_game = True
        other.save(update_fields=['in_game'])
        other.faction_assignments.create(faction=self.greek)
        guard = self.mob('guard')
        encounter = engage(self.player, guard)[0]
        engage(other, guard)
        CombatEncounter.objects.filter(pk=encounter.pk).update(resolution_interval=-1, next_resolution_ts=None)
        def first(ctx):
            ctx.participant(self.player.key).intent_ready = True
            self.assertFalse(resolve_locked(ctx, ctx.encounters[encounter.pk], auto_advance=False).events)
            ctx.participant(other.key).intent_ready = True
            return resolve_locked(ctx, ctx.encounters[encounter.pk], auto_advance=False)
        result = transact(first, encounter_ids=[encounter.pk])
        self.assertEqual(len([e for e in result.events if e.type == 'notification.combat.attack']), 3)
        self.assertFalse(CombatParticipant.objects.filter(encounter=encounter, intent_ready=True).exists())

    def test_player_disconnect_does_not_remove_membership_or_stop_turns(self):
        encounter = engage(self.player, self.mob('guard'))[0]
        self.player.in_game = False
        self.player.save(update_fields=['in_game'])
        result = resolve(encounter.pk, auto_advance=False)
        self.assertTrue(result.encounter_active)
        self.assertTrue(CombatParticipant.objects.filter(player=self.player, is_active=True).exists())

    def test_npc_final_blow_rewards_active_contributor_once(self):
        from spawns.combat_rounds import _defeat, _record_damage
        commander = self.mob('commander', self.greek)
        guard = self.mob('guard', exp_worth=11)
        encounter = engage(self.player, guard)[0]
        engage(commander, guard)
        before = self.player.experience
        guard_id = guard.pk
        def defeat(ctx):
            target = ctx.actors[guard.key]
            _record_damage(ctx, ctx.actors[self.player.key], target, 1)
            target.health = 0
            target.save(update_fields=['health'])
            return _defeat(ctx, ctx.encounters[encounter.pk], ctx.participant(guard.key),
                           ctx.actors[commander.key], 'reward-test')
        transact(defeat, encounter_ids=[encounter.pk])
        self.player.refresh_from_db()
        self.assertEqual(self.player.experience, before + 11)
        self.assertEqual(CombatRewardReceipt.objects.filter(mob_runtime_id=guard_id).count(), 1)
        resolve(encounter.pk, auto_advance=False)
        self.player.refresh_from_db()
        self.assertEqual(self.player.experience, before + 11)

    def test_npc_only_defeat_creates_no_rewards_or_corpse(self):
        from spawns.combat_rounds import _defeat
        from spawns.models import Item
        left, right = self.mob('commander', self.greek), self.mob('guard', exp_worth=20)
        encounter = engage(left, right)[0]
        corpses = Item.objects.count()
        transact(lambda ctx: _defeat(ctx, ctx.encounters[encounter.pk], ctx.participant(right.key),
                                    ctx.actors[left.key], 'npc-defeat'), encounter_ids=[encounter.pk])
        receipt = CombatRewardReceipt.objects.get(mob_runtime_id=right.pk)
        self.assertEqual(receipt.awards, [])
        self.assertEqual(Item.objects.count(), corpses)

    def test_deferred_database_constraints_reject_cross_encounter_side_and_target(self):
        import importlib
        from django.db import connection
        module = importlib.import_module('spawns.migrations.0162_combat_scope_constraints')
        with connection.cursor() as cursor:
            cursor.execute(module.FORWARD_SQL)
        first = engage(self.player, self.mob('guard'))[0]
        second = engage(self.mob('commander', self.greek), self.mob('other guard'))[0]
        member = CombatParticipant.objects.get(player=self.player, is_active=True)
        other = second.participants.filter(mob__isnull=False).first()
        for changes in [{'side_id': other.side_id}, {'current_target_id': other.pk}]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                CombatParticipant.objects.filter(pk=member.pk).update(**changes)
                with connection.cursor() as cursor:
                    cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        with self.assertRaises(IntegrityError), transaction.atomic():
            CombatEncounter.objects.filter(pk=first.pk).update(status='finished')
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')

    def test_round_query_growth_is_bounded(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from time import perf_counter
        from spawns.combat_rounds import finish_encounter
        samples = []
        for count in (2, 8, 32):
            guard = self.mob(f'guard {count}')
            encounter = engage(self.player, guard)[0]
            for index in range(count - 2):
                engage(self.mob(f'guard {count}:{index}'), self.player)
            start = perf_counter()
            with CaptureQueriesContext(connection) as queries:
                result = resolve(encounter.pk, auto_advance=False)
            samples.append((count, len(queries), round((perf_counter() - start) * 1000)))
            self.assertLessEqual(len([e for e in result.events if e.type == 'notification.combat.snapshot']), 1)
            transact(lambda ctx: finish_encounter(ctx, ctx.encounters[encounter.pk]), encounter_ids=[encounter.pk])
        print('Combat round profile (participants, queries, milliseconds):', samples)
        self.assertLess(samples[-1][1], samples[0][1] + 30 * 30)


from django.test import TransactionTestCase


class CombatAdmissionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from worlds.models import World, WorldConfig
        from spawns.models import Player
        from config import constants
        self.enterContext(patch('spawns.combat_reconciliation.request_reconciliation'))
        user = get_user_model().objects.create_user('concurrency@example.com', 'test')
        authored = World.objects.new_world(name='Concurrency', author=user, config=WorldConfig.objects.create())
        world = authored.create_spawn_world()
        world.lifecycle = constants.WORLD_LIFECYCLE_RUNNING
        world.save(update_fields=['lifecycle'])
        room = authored.zones.first().rooms.first()
        self.players = [Player.objects.create(name=f'Player {i}', user=user, world=world, room=room,
                                              health=1000, group_id='allies') for i in range(2)]
        self.mobs = [Mob.objects.create(name=f'Guard {i}', world=world, room=room, health=1000,
                                        health_max=1000, group_id='guards') for i in range(2)]

    def race(self, pairs):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections, close_old_connections
        gate = Barrier(2)
        def start(pair):
            close_old_connections()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '3s'")
                gate.wait(timeout=5)
                return engage(*pair)[0].pk
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(start, pair) for pair in pairs]
            return [future.result(timeout=10) for future in futures]

    def test_two_players_admitting_the_same_mob_share_one_encounter(self):
        ids = self.race([(self.players[0], self.mobs[0]), (self.players[1], self.mobs[0])])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(CombatParticipant.objects.filter(is_active=True).count(), 3)
        self.assertEqual(CombatParticipant.objects.filter(mob=self.mobs[0], is_active=True).count(), 1)

    def test_two_mobs_admitting_the_same_player_share_one_encounter(self):
        ids = self.race([(self.players[0], self.mobs[0]), (self.players[0], self.mobs[1])])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(CombatParticipant.objects.filter(is_active=True).count(), 3)
