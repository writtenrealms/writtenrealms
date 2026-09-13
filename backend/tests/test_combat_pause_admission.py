from unittest.mock import patch
from time import perf_counter

from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.combat_reconciliation import reconcile
from spawns.instance_time import advance
from spawns.models import ActiveEffect, CombatEncounter, CombatParticipant, CombatRoomState, Mob
from tests.base import WorldTestCase
from tests.utils import apply_basic_stat_system, capture_game_messages, dispatch_text_command
from worlds.instances import create_fresh_instance_run
from worlds.models import World, WorldConfig


class TestCombatPauseAdmission(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.world.config.combat_resolution_interval = 2
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=['combat_resolution_interval', 'default_roam_chance'])
        template = World.objects.new_world(
            name='Ambush', author=self.user, instance_of=self.world, is_multiplayer=True,
            config=WorldConfig.objects.create(instance_single_player=True, instance_time_control=True),
        )
        self.entry = template.config.starting_room
        self.east = self.entry.create_at(adv_consts.DIRECTION_EAST)
        self.run = create_fresh_instance_run(template, leader=self.player)
        self.run.spawned_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        self.run.spawned_world.save(update_fields=['lifecycle'])
        self.player.world = self.run.spawned_world
        self.player.room = self.entry
        self.player.in_game = True
        self.player.health = 100
        self.player.stamina = 100
        self.player.save(update_fields=['world', 'room', 'in_game', 'health', 'stamina'])
        self.messages = self.enterContext(capture_game_messages())
        self.enterContext(patch('spawns.tasks.publish_to_player'))

    def _mob(self, name, **kwargs):
        data = dict(world=self.run.spawned_world, health=100, health_max=100, fights_back=False)
        data.update(kwargs)
        return Mob.objects.create(name=name, **data)

    def _advance(self):
        return advance(run_id=self.run.pk, player_id=self.player.pk)

    def _pack(self, *, extra_hostiles=0, bystanders=0):
        dispatch_text_command(self.player.pk, 'pause')
        common = dict(room=self.east, aggression='players', fights_back=True,
                      group_id='overseer-medizer', health=10000, health_max=10000,
                      attack_power=0)
        overseer = self._mob('Overseer', keywords='overseer', target_priority=-10, **common)
        for index in range(extra_hostiles):
            self._mob(f'Soldier {index}', **common)
        for index in range(bystanders):
            self._mob(f'Bystander {index}', room=self.east, aggression='passive')
        medizer = self._mob('Medizer', keywords='medizer', target_priority=10, **common)
        return overseer, medizer

    def _enter_pack(self):
        dispatch_text_command(self.player.pk, 'east')
        state = CombatRoomState.objects.get(world=self.run.spawned_world, room=self.east)
        for _ in range(64):
            reconcile(state.pk)
            self.run.refresh_from_db()
            if self.run.time_paused:
                return state
        self.fail('Room entry did not pause combat')

    def test_room_entry_selects_medizer_and_admits_whole_pack_before_pause(self):
        overseer, medizer = self._pack()
        self._enter_pack()

        member = CombatParticipant.objects.get(player=self.player, is_active=True)
        self.assertEqual(member.current_target.mob_id, medizer.pk)
        self.assertEqual({p.actor_key for p in member.encounter.participants.filter(is_active=True)},
                         {self.player.key, overseer.key, medizer.key})
        self.assertEqual(member.encounter.round_number, 0)
        self.assertEqual(self.run.simulation_tick, 0)
        self.player.refresh_from_db()
        self.assertEqual(self.player.health, 100)
        self.assertEqual(list(member.encounter.participants.filter(mob__isnull=False)
                              .values_list('mob__health', flat=True)), [10000, 10000])

        # A self-targeted preparation must not make the first NPC admitted
        # become the player's basic-attack target on the opening round.
        from builders.models import AbilityDefinition
        ability = AbilityDefinition.objects.create(
            world=self.world, slug='admission-guard', name='Guard', command_verbs=['guard'],
            target={'type': 'self', 'default': 'self'},
            components=[{'type': 'effect', 'effect': 'guard', 'category': 'buff',
                'target': 'self', 'duration': {'rounds': 3}, 'apply': 'on_resolve',
                'primitives': [{'type': 'stat_modifier', 'stat': 'armor', 'op': 'add', 'amount': 25}]}],
        )
        self.player.known_abilities = [ability.slug]
        self.player.save(update_fields=['known_abilities'])
        dispatch_text_command(self.player.pk, 'guard')
        self._advance()
        self.assertTrue(ActiveEffect.objects.filter(target_player=self.player, effect='guard').exists())
        member.refresh_from_db()
        self.assertEqual(member.current_target.mob_id, medizer.pk)
        member.encounter.refresh_from_db()
        self.assertEqual(member.encounter.round_number, 1)

    def test_opening_admission_crosses_admission_and_candidate_page_limits(self):
        # The last/highest-priority hostile lies beyond both the eight-join
        # work slice and the 32-candidate page; neither may end the opening.
        _, medizer = self._pack(extra_hostiles=8, bystanders=32)
        self._enter_pack()
        member = CombatParticipant.objects.get(player=self.player, is_active=True)
        self.assertEqual(member.current_target.mob_id, medizer.pk)
        self.assertEqual(member.encounter.participants.filter(is_active=True).count(), 11)
        self.assertEqual(member.encounter.round_number, 0)

    def test_explicit_target_choice_survives_automatic_admission(self):
        overseer, medizer = self._pack()
        dispatch_text_command(self.player.pk, 'east')
        dispatch_text_command(self.player.pk, 'kill overseer')
        self.run.refresh_from_db()
        self.assertTrue(self.run.time_paused)
        self._advance()
        member = CombatParticipant.objects.get(player=self.player, is_active=True)
        self.assertEqual(member.current_target.mob_id, overseer.pk)
        self.assertTrue(member.encounter.participants.filter(mob=medizer, is_active=True).exists())

    def test_background_reconciliation_cannot_admit_mobs_after_pause(self):
        self._pack()
        state = self._enter_pack()
        newcomer = self._mob('Newcomer', room=self.east, aggression='players',
                             group_id='overseer-medizer', fights_back=True)
        self.assertEqual(reconcile(state.pk), 0)
        self.assertFalse(CombatParticipant.objects.filter(mob=newcomer, is_active=True).exists())

    def test_opening_query_count_does_not_grow_with_other_rooms(self):
        self._pack()
        dispatch_text_command(self.player.pk, 'east')
        state = CombatRoomState.objects.get(world=self.run.spawned_world, room=self.east)
        samples = []
        for count in (0, 100):
            if count:
                Mob.objects.bulk_create([Mob(world=self.run.spawned_world, room=self.entry,
                    name=f'Offscreen {index}', health=100, health_max=100) for index in range(count)])
            # Repeat the same opening with more offscreen actors, without
            # measuring setup or reusing the already completed encounter.
            with transaction.atomic():
                start = perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    reconcile(state.pk)
                samples.append((count, len(queries), round((perf_counter() - start) * 1000)))
                self.run.refresh_from_db()
                self.assertTrue(self.run.time_paused)
                transaction.set_rollback(True)
        print('Combat opening profile (offscreen mobs, queries, milliseconds):', samples)
        self.assertEqual(samples[0][1], samples[1][1])

    def test_opening_work_budget_rolls_back_partial_admission(self):
        self._pack()
        dispatch_text_command(self.player.pk, 'east')
        state = CombatRoomState.objects.get(world=self.run.spawned_world, room=self.east)
        # Start on the mob target page so the budget is exhausted after an
        # actual admission, rather than while skipping the player page.
        state.cursor = {'source_kind': 'player', 'source_after': 0,
                        'target_kind': 'mob', 'target_after': 0}
        state.frozen_actors = [self.player.key]
        state.frozen_generation = state.dirty_generation
        state.save(update_fields=['cursor', 'frozen_actors', 'frozen_generation'])
        with patch('spawns.combat_reconciliation.MAX_OPENING_PAGES', 1):
            with self.assertRaises(ActionError) as error:
                reconcile(state.pk)
        self.assertEqual(error.exception.code, 'instance_turn_budget')
        self.assertFalse(CombatEncounter.objects.filter(world=self.run.spawned_world).exists())
        self.run.refresh_from_db()
        self.assertFalse(self.run.time_paused)
        state.refresh_from_db()
        self.assertIsNone(state.lease_until)
