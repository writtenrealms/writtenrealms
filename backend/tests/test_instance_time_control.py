from datetime import timedelta
from unittest.mock import patch
from time import perf_counter

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.combat_rounds import resolve
from spawns.handlers import dispatch_command
from spawns.instance_clock import SIMULATION_STEP_SECONDS, gameplay_now, live_worlds
from spawns.instance_time import advance, cancel, configure, snapshot_for_player
from spawns.models import CombatEncounter, InstanceClockWork, Mob, Player
from spawns.tasks import WR2_STANDING_REGEN_RATE, run_game_heartbeat, resume_instance_schedulers
from tests.base import WorldTestCase
from tests.utils import apply_basic_stat_system, capture_game_messages, create_active_effect, dispatch_text_command
from worlds.instances import create_fresh_instance_run, reset_instance
from worlds.models import InstanceRun, World, WorldConfig


class TestInstanceTimeControl(WorldTestCase):
    def setUp(self):
        super().setUp()
        apply_basic_stat_system(self.world)
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.world.config.combat_resolution_interval = 2
        self.world.config.default_roam_chance = 0
        self.world.config.save(update_fields=['combat_resolution_interval', 'default_roam_chance'])
        self.template = World.objects.new_world(
            name='Thinking Room', author=self.user, instance_of=self.world, is_multiplayer=True,
            config=WorldConfig.objects.create(instance_single_player=True, instance_time_control=True),
        )
        self.entry = self.template.config.starting_room
        self.east = self.entry.create_at(adv_consts.DIRECTION_EAST)
        self.run = self._make_run(self.player)
        self.messages = self.enterContext(capture_game_messages())
        self.enterContext(patch('spawns.tasks.publish_to_player'))

    def _make_run(self, player):
        run = create_fresh_instance_run(self.template, leader=player)
        run.spawned_world.lifecycle = adv_consts.WORLD_LIFECYCLE_RUNNING
        run.spawned_world.save(update_fields=['lifecycle'])
        player.world = run.spawned_world
        player.room = self.entry
        player.in_game = True
        player.health = 100
        player.energy = 0
        player.stamina = 50
        player.save(update_fields=['world', 'room', 'in_game', 'health', 'energy', 'stamina'])
        return run

    def _mob(self, name='Observer', room=None, run=None, **kwargs):
        data = dict(name=name, world=(run or self.run).spawned_world, room=room or self.entry,
                    health=100, health_max=200, energy=0, energy_max=100, stamina=50, stamina_max=100,
                    fights_back=False)
        data.update(kwargs)
        return Mob.objects.create(**data)

    def _fight(self, *, paused=True, health=10000):
        configure(self.player.pk, pause_in_combat=paused)
        mob = self._mob('Opponent', health=health, health_max=max(health, 200))
        dispatch_text_command(self.player.pk, 'kill opponent')
        self.run.refresh_from_db()
        self.assertEqual(self.run.time_paused, paused)
        return mob, CombatEncounter.objects.get(world=self.run.spawned_world, status='active')

    def _advance(self, run=None, player=None, **kwargs):
        run = run or self.run
        run.refresh_from_db()
        args = dict(run_id=run.pk, player_id=(player or self.player).pk,
                    expected_tick=run.simulation_tick, expected_generation=run.time_generation,
                    expected_pending_revision=run.pending_revision)
        args.update(kwargs)
        return advance(**args)

    def test_permission_alone_keeps_normal_commands_and_heartbeat(self):
        self.assertFalse(self.run.pause_in_combat)
        self.assertFalse(self.run.time_paused)
        dispatch_text_command(self.player.pk, 'east')
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.east.pk)
        self.run.refresh_from_db()
        self.assertFalse(self.run.pending_command)
        self.assertEqual(self.run.simulation_tick, 0)
        before = self.player.stamina
        run_game_heartbeat()
        self.player.refresh_from_db()
        self.assertGreater(self.player.stamina, before)
        with self.assertRaises(ActionError) as error:
            self._advance()
        self.assertEqual(error.exception.code, 'instance_not_paused')

    def test_arming_pause_does_not_pause_exploration_or_queue_movement(self):
        dispatch_text_command(self.player.pk, 'pause')
        dispatch_text_command(self.player.pk, 'east')
        self.run.refresh_from_db()
        self.player.refresh_from_db()
        self.assertTrue(self.run.pause_in_combat)
        self.assertFalse(self.run.time_paused)
        self.assertEqual(self.player.room_id, self.east.pk)
        self.assertFalse(self.run.pending_command)
        self.assertIsNotNone(snapshot_for_player(self.player))

    def test_combat_freezes_before_opening_round_and_preserves_inherited_interval(self):
        self.world.config.combat_resolution_interval = 5
        self.world.config.save(update_fields=['combat_resolution_interval'])
        mob, encounter = self._fight()
        self.assertEqual(encounter.resolution_interval, 5)
        before = (self.player.health, mob.health)
        resolve(encounter.pk, auto_advance=True, durable_events=True)
        encounter.refresh_from_db()
        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual(encounter.round_number, 0)
        self.assertEqual((self.player.health, mob.health), before)
        self._advance()
        encounter.refresh_from_db()
        self.assertEqual(encounter.round_number, 1)

    def test_background_heartbeat_freezes_every_room_and_character_effects(self):
        self._fight()
        mob = self._mob('Elsewhere', room=self.east)
        self.player.ability_cooldowns = {'power-strike': 3}
        self.player.save(update_fields=['ability_cooldowns'])
        effect = create_active_effect(target=self.player, source=self.player, payload={'effect': 'crest', 'remaining_rounds': 3})
        effect.next_tick_ts = self.run.simulation_time - timedelta(seconds=30)
        effect.save(update_fields=['next_tick_ts'])
        for _ in range(3):
            run_game_heartbeat()
        self.player.refresh_from_db()
        mob.refresh_from_db()
        effect.refresh_from_db()
        self.assertEqual(self.player.ability_cooldowns, {'power-strike': 3})
        self.assertEqual((mob.health, mob.energy, mob.stamina), (100, 0, 50))
        self.assertEqual(effect.remaining_rounds, 3)
        self.assertEqual(self.run.simulation_tick, 0)

    def test_automatic_ambush_waits_before_its_first_round(self):
        from spawns.combat_reconciliation import reconcile
        from spawns.models import CombatRoomState
        dispatch_text_command(self.player.pk, 'pause')
        mob = self._mob('Ambusher', room=self.east, aggression=adv_consts.MOB_AGGRESSION_PLAYERS,
                        fights_back=True)
        dispatch_text_command(self.player.pk, 'east')
        state = CombatRoomState.objects.get(world=self.run.spawned_world, room=self.east)
        for _ in range(8):
            reconcile(state.pk)
            self.run.refresh_from_db()
            if self.run.time_paused:
                break
        self.assertTrue(self.run.time_paused)
        encounter = CombatEncounter.objects.get(world=self.run.spawned_world, status='active')
        self.assertEqual(encounter.round_number, 0)
        self.player.refresh_from_db()
        mob.refresh_from_db()
        self.assertEqual((self.player.health, mob.health), (100, 100))
        self._advance()
        encounter.refresh_from_db()
        self.assertEqual(encounter.round_number, 1)

    def test_unchecked_preserves_inherited_command_driven_combat(self):
        self.world.config.combat_resolution_interval = -1
        self.world.config.save(update_fields=['combat_resolution_interval'])
        _, encounter = self._fight(paused=False)
        self.assertEqual(encounter.resolution_interval, -1)
        self.assertEqual(encounter.round_number, 1)
        self.assertFalse(self.run.time_paused)
        self.assertEqual(self.run.simulation_tick, 0)

    def test_one_advance_resolves_one_round_and_offscreen_recovery_once(self):
        _, encounter = self._fight()
        self.player.ability_cooldowns = {'power-strike': 3}
        self.player.save(update_fields=['ability_cooldowns'])
        mob = self._mob('Elsewhere', room=self.east)
        before = self.run.simulation_time
        self._advance()
        self.run.refresh_from_db()
        self.player.refresh_from_db()
        mob.refresh_from_db()
        encounter.refresh_from_db()
        self.assertTrue(self.run.time_paused)
        self.assertEqual(encounter.round_number, 1)
        self.assertEqual(self.player.ability_cooldowns, {'power-strike': 2})
        self.assertEqual(mob.stamina, 50 + WR2_STANDING_REGEN_RATE)
        self.assertEqual(self.run.simulation_time, before + timedelta(seconds=SIMULATION_STEP_SECONDS))

    def test_commands_prepare_only_while_paused_and_inspection_preserves_preparation(self):
        self._fight()
        dispatch_text_command(self.player.pk, 'kill opponent')
        self.run.refresh_from_db()
        revision = self.run.pending_revision
        self.assertTrue(self.run.pending_command)
        dispatch_text_command(self.player.pk, 'look')
        dispatch_text_command(self.player.pk, 'quest info no-such-quest')
        self.run.refresh_from_db()
        self.assertEqual(self.run.pending_revision, revision)
        self.assertEqual(self.run.simulation_tick, 0)
        self._advance()
        self.run.refresh_from_db()
        self.assertFalse(self.run.pending_command)

    def test_hotkey_resolves_when_prepared_without_echoing_again_on_advance(self):
        from builders.models import AbilityDefinition
        self._fight()
        AbilityDefinition.objects.create(
            world=self.world, slug='test-strike', name='Test Strike', command_verbs=['strike'],
            target={'type': 'hostile', 'default': 'current_target'},
            components=[{'type': 'damage', 'profile': 'basic_physical'}],
        )
        self.player.known_abilities = ['test-strike']
        self.player.ability_hotkeys = {'1': 'test-strike'}
        self.player.save(update_fields=['known_abilities', 'ability_hotkeys'])
        dispatch_command('text', player_id=self.player.pk, payload={'text': '1 opponent', '_request_id': 'prepared-hotkey'})
        self.run.refresh_from_db()
        self.assertEqual(self.run.simulation_tick, 0)
        resolutions = lambda: [row['message'] for row in self.messages if row['message']['type'] == 'cmd.ability.hotkey.resolve']
        self.assertEqual([message['text'] for message in resolutions()], ['1 opponent -> strike opponent'])
        self.assertEqual(resolutions()[0]['data']['request_id'], 'prepared-hotkey')
        self._advance()
        self.assertEqual(len(resolutions()), 1)

    def test_resume_preserves_remaining_combat_delay_and_schedules_normal_rounds(self):
        _, encounter = self._fight()
        self.run.refresh_from_db()
        old_generation = encounter.schedule_generation
        remaining = encounter.next_resolution_ts - self.run.simulation_time
        resumed_at = self.run.simulation_time + timedelta(minutes=10)
        with patch('django.utils.timezone.now', return_value=resumed_at):
            state = configure(self.player.pk, pause_in_combat=False)
        encounter.refresh_from_db()
        self.run.refresh_from_db()
        self.assertFalse(state['paused'])
        self.assertEqual(encounter.resolution_interval, 2)
        self.assertEqual(encounter.next_resolution_ts - resumed_at, remaining)
        self.assertGreater(encounter.schedule_generation, old_generation)
        with patch('spawns.tasks.resolve_combat_encounter.apply_async') as schedule, self.captureOnCommitCallbacks(execute=True):
            resume_instance_schedulers.run(self.run.pk, self.run.time_generation)
        self.assertTrue(schedule.called)
        with patch('django.utils.timezone.now', return_value=encounter.next_resolution_ts):
            resolve(encounter.pk, auto_advance=True, expected_generation=encounter.schedule_generation)
        encounter.refresh_from_db()
        self.assertEqual(encounter.round_number, 1)

    def test_pause_mid_fight_invalidates_old_scheduled_round(self):
        _, encounter = self._fight(paused=False)
        old_generation = encounter.schedule_generation
        configure(self.player.pk, pause_in_combat=True)
        result = resolve(encounter.pk, auto_advance=True, expected_generation=old_generation)
        encounter.refresh_from_db()
        self.assertEqual(encounter.round_number, 0)
        self.assertFalse(result.events)
        self.assertGreater(encounter.schedule_generation, old_generation)

    def test_unchecking_submits_the_prepared_action_without_a_synthetic_turn(self):
        _, encounter = self._fight()
        dispatch_text_command(self.player.pk, 'kill opponent')
        state = configure(self.player.pk, pause_in_combat=False)
        self.assertFalse(state['pending_command'])
        self.assertFalse(state['paused'])
        self.assertEqual(state['tick'], 0)
        encounter.refresh_from_db()
        self.assertEqual(encounter.round_number, 0)
        self.assertTrue(encounter.participants.get(player=self.player).intent_ready)

    def test_combat_end_resumes_exploration_while_preference_stays_enabled(self):
        self._fight()
        dispatch_text_command(self.player.pk, 'disengage opponent')
        self._advance()
        self.run.refresh_from_db()
        self.assertFalse(self.run.time_paused)
        self.assertTrue(self.run.pause_in_combat)
        dispatch_text_command(self.player.pk, 'east')
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.east.pk)
        self.run.refresh_from_db()
        self.assertFalse(self.run.pending_command)

    def test_quick_text_pause_resume_and_toggle(self):
        self._fight()
        for command, expected in [('resume', False), ('pause', True), ('time toggle', False), ('time on', True)]:
            dispatch_text_command(self.player.pk, command)
            self.run.refresh_from_db()
            self.assertEqual(self.run.pause_in_combat, expected)
            self.assertEqual(self.run.time_paused, expected)
            self.assertEqual(self.run.simulation_tick, 0)

    def test_invalid_preparation_rolls_back_the_entire_turn(self):
        self._fight()
        before = self.run.simulation_time
        dispatch_text_command(self.player.pk, 'north')
        with self.assertRaises(ActionError):
            self._advance()
        self.run.refresh_from_db()
        self.assertEqual(self.run.simulation_time, before)
        self.assertEqual(self.run.simulation_tick, 0)
        self.assertTrue(self.run.pending_command)

    def test_stale_or_duplicate_advance_cannot_consume_another_turn(self):
        self._fight()
        self._advance(request_id='one-click')
        with self.assertRaises(ActionError):
            self._advance(expected_tick=0)
        result = advance(run_id=self.run.pk, player_id=self.player.pk, request_id='one-click')
        self.assertEqual(result['tick'], 1)

    def test_stale_preparation_cancel_and_preference_are_fenced(self):
        self._fight()
        dispatch_text_command(self.player.pk, 'kill opponent')
        self.run.refresh_from_db()
        revision, generation = self.run.pending_revision, self.run.time_generation
        dispatch_text_command(self.player.pk, 'disengage opponent')
        with self.assertRaises(ActionError):
            self._advance(expected_pending_revision=revision)
        with self.assertRaises(ActionError):
            cancel(self.player.pk, expected_pending_revision=revision)
        configure(self.player.pk, pause_in_combat=False)
        with self.assertRaises(ActionError):
            configure(self.player.pk, pause_in_combat=True, expected_generation=generation)

    def test_non_owner_and_outside_player_cannot_control_clock(self):
        self._fight()
        other = self.create_player('Other Owner')
        with self.assertRaises(ActionError):
            self._advance(player=other)
        with self.assertRaises(ActionError):
            configure(other.pk, pause_in_combat=True)
        with self.assertRaises(ActionError):
            configure(self.player.pk, pause_in_combat='yes')

    def test_reconnect_preserves_combat_pause_without_catchup(self):
        from spawns.instance_time import resume_for_player
        self._fight()
        tick, simtime = self.run.simulation_tick, self.run.simulation_time
        with patch('django.utils.timezone.now', return_value=simtime + timedelta(days=1)):
            resume_for_player(self.player)
        self.run.refresh_from_db()
        self.assertTrue(self.run.time_paused)
        self.assertEqual((self.run.simulation_tick, self.run.simulation_time), (tick, simtime))

    def test_background_query_cost_does_not_grow_with_paused_worlds(self):
        self._fight()
        run_game_heartbeat()
        with CaptureQueriesContext(connection) as first:
            run_game_heartbeat()
        for index in range(8):
            run = self._make_run(self.create_player(f'Waiting {index}'))
            InstanceRun.objects.filter(pk=run.pk).update(time_paused=True)
        with CaptureQueriesContext(connection) as many:
            run_game_heartbeat()
        self.assertLessEqual(len(many), len(first) + 1)
        with self.assertNumQueries(1):
            self.assertEqual(list(live_worlds(Player.objects.filter(in_game=True))), [])

    def test_turn_query_growth_is_bounded_across_offscreen_mobs(self):
        self._fight()
        samples, previous_count = [], 0
        for count in (1, 16, 64):
            for index in range(previous_count, count):
                self._mob(f'Quiet observer {index}', room=self.east)
            self._advance()
            Mob.objects.filter(world=self.run.spawned_world, room=self.east).update(health=100, energy=0, stamina=50)
            start = perf_counter()
            with CaptureQueriesContext(connection) as queries:
                self._advance()
            samples.append((count, len(queries), round((perf_counter() - start) * 1000)))
            self.assertFalse(Mob.objects.filter(world=self.run.spawned_world, room=self.east).exclude(stamina=50 + WR2_STANDING_REGEN_RATE).exists())
            previous_count = count
        print('Combat-pause turn profile (offscreen mobs, queries, milliseconds):', samples)
        self.assertLessEqual(samples[-1][1], samples[0][1] + 8)
