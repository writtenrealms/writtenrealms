from datetime import timedelta
from unittest.mock import patch
from time import perf_counter

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from builders.models import AbilityDefinition
from config import constants as adv_consts
from spawns.actions.base import ActionError
from spawns.combat_rounds import resolve
from spawns.handlers import dispatch_command
from spawns.instance_clock import SIMULATION_STEP_SECONDS, gameplay_now, live_worlds
from spawns.instance_time import advance, cancel, configure, snapshot_for_player
from spawns.models import ActiveEffect, CombatEncounter, InstanceClockWork, Mob, Player
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

    def _turn_abilities(self):
        abilities = [AbilityDefinition.objects.create(
            world=self.world, slug=slug, name=name, command_verbs=[verb],
            cast_time={'rounds': rounds}, cost={'resource': 'energy', 'amount': 5},
            target={'type': 'hostile', 'default': 'current_target'},
            components=[{'type': 'damage', 'profile': 'basic_physical'}],
        ) for slug, name, verb, rounds in (
            ('test-trident', 'Test Trident', 'tri', 1), ('test-tide', 'Test Tide', 'tide', 0),
        )]
        self.player.known_abilities = [ability.slug for ability in abilities]
        self.player.ability_hotkeys = {'3': abilities[0].slug, '4': abilities[1].slug}
        self.player.energy = 100
        self.player.save(update_fields=['known_abilities', 'ability_hotkeys', 'energy'])
        return abilities

    def _charging_fight(self):
        _, encounter = self._fight()
        self._turn_abilities()
        dispatch_text_command(self.player.pk, 'tri')
        self._advance()
        participant = encounter.participants.get(player=self.player)
        self.assertEqual(participant.pending_ability['status'], 'casting')
        return encounter, participant

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

    def test_manual_round_effect_timestamps_use_simulation_time_before_resuming(self):
        self._fight()
        effects = [create_active_effect(target=self.player, source=self.player, payload=payload)
                   for payload in (
                       {'effect': 'crest', 'remaining_rounds': 3},
                       {'effect': 'renewal', 'remaining_rounds': 3, 'tick': {
                           'every_rounds': 1, 'primitives': [{'type': 'resource_change',
                           'resource': 'energy', 'amount': 1, 'target': 'effect.target'}],
                       }},
                   )]
        resumed_at = self.run.simulation_time + timedelta(minutes=10)
        with patch('django.utils.timezone.now', return_value=resumed_at):
            self._advance()
            self.run.refresh_from_db()
            for effect in effects:
                effect.refresh_from_db()
                self.assertEqual(effect.remaining_rounds, 2)
                self.assertEqual(effect.last_tick_ts, self.run.simulation_time)
                self.assertEqual(effect.next_tick_ts, self.run.simulation_time + timedelta(microseconds=1))
            configure(self.player.pk, pause_in_combat=False)
        for effect in effects:
            effect.refresh_from_db()
            self.assertEqual(effect.last_tick_ts, resumed_at)
            self.assertEqual(effect.next_tick_ts, resumed_at + timedelta(microseconds=1))

    def test_reset_of_paused_combat_resumes_effects_and_cooldowns_on_next_heartbeat(self):
        self._fight()
        self.player.ability_cooldowns = {'crest': 2}
        self.player.save(update_fields=['ability_cooldowns'])
        effect = create_active_effect(target=self.player, source=self.player,
                                      payload={'effect': 'crest', 'remaining_rounds': 2})
        resumed_at = self.run.simulation_time + timedelta(minutes=10)
        with patch('django.utils.timezone.now', return_value=resumed_at):
            reset_instance(player=self.player)
        self.run.refresh_from_db()
        effect.refresh_from_db()
        self.assertFalse(self.run.time_paused)
        self.assertEqual(self.run.clock_offset_seconds, 600)
        self.assertFalse(CombatEncounter.objects.filter(world=self.run.spawned_world, status='active').exists())
        self.assertLessEqual(effect.next_tick_ts, resumed_at)
        for offset, remaining in ((0.1, 1), (2.1, 0)):
            with patch('django.utils.timezone.now', return_value=resumed_at + timedelta(seconds=offset)):
                run_game_heartbeat()
            self.player.refresh_from_db()
            self.assertEqual(self.player.ability_cooldowns.get('crest', 0), remaining)
            self.assertEqual(ActiveEffect.objects.filter(pk=effect.pk).values_list('remaining_rounds', flat=True).first() or 0,
                             remaining)

    def test_winning_a_manual_round_resumes_remaining_effects_and_cooldowns(self):
        self._fight(health=1)
        self.player.ability_cooldowns = {'crest': 3}
        self.player.save(update_fields=['ability_cooldowns'])
        effect = create_active_effect(target=self.player, source=self.player,
                                      payload={'effect': 'crest', 'remaining_rounds': 3})
        resumed_at = self.run.simulation_time + timedelta(minutes=10)
        with patch('django.utils.timezone.now', return_value=resumed_at):
            self._advance()
        self.run.refresh_from_db()
        self.player.refresh_from_db()
        effect.refresh_from_db()
        self.assertFalse(self.run.time_paused)
        self.assertLessEqual(effect.next_tick_ts, resumed_at + timedelta(microseconds=1))
        cooldown, duration = self.player.ability_cooldowns['crest'], effect.remaining_rounds
        with patch('django.utils.timezone.now', return_value=resumed_at + timedelta(seconds=0.1)):
            run_game_heartbeat()
        self.player.refresh_from_db()
        effect.refresh_from_db()
        self.assertEqual(self.player.ability_cooldowns.get('crest', 0), cooldown - 1)
        self.assertEqual(effect.remaining_rounds, duration - 1)

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
        self.assertEqual(self.run.pending_command['label'], 'strike opponent')
        confirmations = [row['message']['text'] for row in self.messages if row['message']['type'] == 'cmd.prepare_turn.success']
        self.assertEqual(confirmations, ['Action: strike opponent.'])
        self._advance()
        self.assertEqual(len(resolutions()), 1)

    def test_charging_rejects_new_actions_from_verbs_hotkeys_and_aliases_before_queueing(self):
        encounter, participant = self._charging_fight()
        dispatch_text_command(self.player.pk, 'alias wave tide')
        self.run.refresh_from_db()
        revision, tick = self.run.pending_revision, self.run.simulation_tick
        cast = dict(participant.pending_ability)
        for command in ('tide', 'test-tide', '4', 'wave', 'tri', '3'):
            with self.subTest(command=command):
                before = len(self.messages)
                dispatch_text_command(self.player.pk, command)
                errors = [row['message'] for row in self.messages[before:]
                          if row['message']['type'] == 'cmd.ability.error']
                self.assertEqual([error['data']['code'] for error in errors], ['ability_cast_in_progress'])
                self.run.refresh_from_db()
                participant.refresh_from_db()
                self.assertFalse(self.run.pending_command)
                self.assertEqual((self.run.pending_revision, self.run.simulation_tick), (revision, tick))
                self.assertEqual(participant.pending_ability, cast)
        self._advance()
        participant.refresh_from_db()
        encounter.refresh_from_db()
        self.assertFalse(participant.pending_ability)
        self.assertEqual(encounter.round_number, 2)

    def test_rejected_ability_preserves_queued_flee_and_flee_cancels_cast(self):
        encounter, participant = self._charging_fight()
        dispatch_text_command(self.player.pk, 'flee')
        self.run.refresh_from_db()
        queued, revision = dict(self.run.pending_command), self.run.pending_revision
        self.assertEqual(queued['command_type'], 'flee')
        dispatch_text_command(self.player.pk, 'tide')
        self.run.refresh_from_db()
        self.assertEqual(self.run.pending_command, queued)
        self.assertEqual(self.run.pending_revision, revision)
        self._advance()
        participant.refresh_from_db()
        self.assertFalse(participant.pending_ability)
        self.assertEqual(participant.pending_flee['status'], 'ready')

    def test_clearing_an_old_invalid_action_preserves_the_cast_and_unblocks_advance(self):
        encounter, participant = self._charging_fight()
        # Reproduce a choice saved before preparation validation was introduced.
        InstanceRun.objects.filter(pk=self.run.pk).update(pending_command={
            'command_type': 'text', 'payload': {'text': 'tide'}, 'label': 'tide',
        })
        with self.assertRaises(ActionError) as error:
            self._advance()
        self.assertEqual(error.exception.code, 'ability_cast_in_progress')
        cast = dict(participant.pending_ability)
        before = self.run.simulation_time
        dispatch_text_command(self.player.pk, 'cancelturn')
        self.run.refresh_from_db()
        participant.refresh_from_db()
        self.assertFalse(self.run.pending_command)
        self.assertEqual(self.run.simulation_time, before)
        self.assertEqual(participant.pending_ability, cast)
        self._advance()
        participant.refresh_from_db()
        encounter.refresh_from_db()
        self.assertFalse(participant.pending_ability)
        self.assertEqual(encounter.round_number, 2)

    def test_unready_abilities_do_not_replace_the_previous_action_or_spend_resources(self):
        _, encounter = self._fight()
        _, ability = self._turn_abilities()
        dispatch_text_command(self.player.pk, 'kill opponent')
        self.run.refresh_from_db()
        queued, revision = dict(self.run.pending_command), self.run.pending_revision
        for changes, code in (
            ({'known_abilities': []}, 'ability_unknown'),
            ({'ability_cooldowns': {ability.slug: 2}}, 'ability_on_cooldown'),
            ({'energy': 0}, 'insufficient_resource'),
        ):
            with self.subTest(code=code):
                defaults = {'known_abilities': ['test-trident', ability.slug], 'ability_cooldowns': {}, 'energy': 100}
                Player.objects.filter(pk=self.player.pk).update(**{**defaults, **changes})
                before = len(self.messages)
                dispatch_text_command(self.player.pk, 'tide')
                errors = [row['message'] for row in self.messages[before:]
                          if row['message']['type'] == 'cmd.ability.error']
                self.assertEqual([error['data']['code'] for error in errors], [code])
                self.run.refresh_from_db()
                self.assertEqual((self.run.pending_command, self.run.pending_revision), (queued, revision))
                self.assertEqual(self.run.simulation_tick, 0)
        Player.objects.filter(pk=self.player.pk).update(energy=100)
        ability.availability = {'min_level': self.player.level + 1}
        ability.save(update_fields=['availability'])
        dispatch_text_command(self.player.pk, 'tide')
        self.run.refresh_from_db()
        self.assertEqual(self.run.pending_command, queued)
        self.assertEqual(self.messages[-1]['message']['data']['code'], 'ability_unavailable')
        ability.availability = {}
        ability.save(update_fields=['availability'])
        dispatch_text_command(self.player.pk, 'tide')
        self.run.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(self.run.pending_command['label'], 'tide')
        self.assertEqual(self.player.energy, 100)
        self.assertFalse(self.player.ability_cooldowns)
        self.assertFalse(encounter.participants.get(player=self.player).pending_ability)

    def test_preparation_cast_check_is_one_indexed_read_independent_of_instance_population(self):
        from spawns.actions.abilities import validate_ability_preparation
        _, participant = self._charging_fight()
        ability = AbilityDefinition.objects.get(world=self.world, slug='test-tide')
        samples = []
        for count in (1, 64):
            if count > 1:
                for index in range(count - 1):
                    self._mob(f'Offscreen {index}', room=self.east)
            start = perf_counter()
            with CaptureQueriesContext(connection) as queries, self.assertRaises(ActionError) as error:
                validate_ability_preparation(self.player, ability)
            self.assertEqual(error.exception.code, 'ability_cast_in_progress')
            self.assertEqual(len(queries), 1)
            self.assertIn('"player_id" =', queries[0]['sql'])
            samples.append((count, len(queries), round((perf_counter() - start) * 1000, 2)))
        print('Preparation cast-check profile (offscreen mobs, queries, milliseconds):', samples)

    def test_normal_timing_does_not_run_preparation_validation(self):
        self._fight(paused=False)
        self._turn_abilities()
        with patch('spawns.actions.abilities.validate_ability_preparation') as validate:
            dispatch_text_command(self.player.pk, 'tri')
        validate.assert_not_called()

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
        for command, expected in [
            ('resume', False), ('resume', False), ('pause', True), ('pause', True),
            ('time toggle', False), ('time toggle', True), ('time resume', False),
            ('time pause', True), ('time off', False), ('time on', True),
        ]:
            with self.subTest(command=command, expected=expected):
                before = len(self.messages)
                dispatch_text_command(self.player.pk, command)
                self.run.refresh_from_db()
                self.assertEqual(self.run.pause_in_combat, expected)
                self.assertEqual(self.run.time_paused, expected)
                self.assertEqual(self.run.simulation_tick, 0)
                confirmations = [row['message'] for row in self.messages[before:]
                                 if row['message']['type'] == 'cmd.time_control.success']
                self.assertEqual(len(confirmations), 1)
                self.assertEqual(confirmations[0].get('text'),
                    'Combat pauses before each round.' if expected else 'Normal world timing is enabled.')
                self.assertEqual(confirmations[0]['data']['pause_in_combat'], expected)

    def test_pause_and_resume_confirm_setting_during_exploration(self):
        for command, expected, text in [
            ('pause', True, 'Combat pauses before each round.'),
            ('resume', False, 'Normal world timing is enabled.'),
        ]:
            with self.subTest(command=command):
                before = len(self.messages)
                dispatch_text_command(self.player.pk, command)
                self.run.refresh_from_db()
                self.assertEqual(self.run.pause_in_combat, expected)
                self.assertFalse(self.run.time_paused)
                confirmations = [row['message'] for row in self.messages[before:]
                                 if row['message']['type'] == 'cmd.time_control.success']
                self.assertEqual([message.get('text') for message in confirmations], [text])

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
