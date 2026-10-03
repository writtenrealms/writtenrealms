from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.db import connection, IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from builders.instance_templates import create_instance_template
from core.condition_dsl import ConditionContext, QUEST_CONDITION_OPERATORS, evaluate_condition
from quests.models import QuestInstance, QuestOfferState, QuestTemplate
from quests.services.discovery import _template_available, list_opportunities, refresh_player_quests
from quests.services.engine import (
    QuestRuntimeError, abandon_instance, accept_template, can_start_template,
    choose_for_instance, info_for_player,
)
from quests.services.progress import progress_player_quests_for_event
from quests.services.quest_log import build_quest_log
from spawns.instance_clock import rebase_character_timers
from spawns.instance_clock_transitions import _rebase_timers
from tests.base import WorldTestCase
from tests.utils import capture_game_messages
from worlds.instances import create_fresh_instance_run, leave_instance
from worlds.models import World


class TestInstanceQuestScope(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=['is_multiplayer'])
        self.player.in_game = True
        self.player.save(update_fields=['in_game'])
        self.template = create_instance_template(
            base_world=self.world, author=self.user, name='Orientation',
        )
        self.quest = QuestTemplate.objects.create(
            world=self.template, slug='orientation', name='Orientation', status='active',
            discovery_policy={'sources': [{'type': 'auto_start'}]},
            graph={'steps': [
                {'id': 'start', 'kind': 'storylet', 'choices': [
                    {'id': 'finish', 'text': 'Finish', 'goto': 'done'},
                ]},
                {'id': 'done', 'kind': 'resolution', 'resolution': 'complete'},
            ]},
        )
        self.run = create_fresh_instance_run(self.template, leader=self.player)
        self.move_to(self.run.spawned_world)
        self.enterContext(capture_game_messages())

    def move_to(self, world):
        self.player.world = world
        self.player.room = (world.context or world).config.starting_room
        self.player.save(update_fields=['world', 'room'])
        # Populate context relations outside query-count assertions.
        self.player.world.context
        self.player.room.zone

    def new_run(self):
        run = create_fresh_instance_run(self.template, leader=self.player)
        self.move_to(run.spawned_world)
        return run

    def states(self, ref=None):
        context = ConditionContext(actor=self.player)
        return tuple(evaluate_condition(
            {operator: self.quest.pk if ref is None else ref}, context=context,
        ) for operator in QUEST_CONDITION_OPERATORS)

    def complete(self):
        attempt = accept_template(self.player, self.quest).quest_instance
        choose_for_instance(self.player, str(attempt.pk), 'finish')
        return attempt

    def test_completed_nonrepeatable_quest_restarts_in_new_run_and_resumes_in_old_run(self):
        first = self.complete()
        self.assertFalse(can_start_template(self.player, self.quest))
        self.assertEqual(self.states(), (True, False, True))
        self.assertEqual(build_quest_log(self.player)['resolved'][0]['id'], first.pk)

        new_run = self.new_run()
        self.assertEqual(self.states(), (False, False, False))
        self.assertTrue(can_start_template(self.player, self.quest))
        self.assertEqual(build_quest_log(self.player)['resolved'], [])
        refresh_player_quests(self.player)
        second = QuestInstance.objects.get(player=self.player, world=new_run.spawned_world)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.current_step_id, 'start')
        self.assertEqual(self.states(), (True, True, False))
        self.assertEqual(QuestOfferState.objects.filter(player=self.player, template=self.quest).count(), 2)

        self.move_to(self.run.spawned_world)
        self.assertEqual(self.states(), (True, False, True))
        self.assertFalse(can_start_template(self.player, self.quest))
        self.assertEqual(build_quest_log(self.player)['resolved'][0]['id'], first.pk)
        self.assertEqual(build_quest_log(self.player)['active'], [])

    def test_daily_completion_is_scoped_to_the_instance_run(self):
        self.quest.repeatability_mode = 'daily'
        self.quest.repeatability_reset_at = '05:00'
        self.quest.repeatability_timezone = 'America/New_York'
        self.quest.save()
        self.complete()
        self.assertFalse(can_start_template(self.player, self.quest))
        self.new_run()
        self.assertTrue(can_start_template(self.player, self.quest))
        self.assertEqual(build_quest_log(self.player)['repeatable'], [])
        self.move_to(self.run.spawned_world)
        self.assertFalse(can_start_template(self.player, self.quest))

    def test_old_active_attempt_cannot_block_or_be_manipulated_from_another_run(self):
        first = accept_template(self.player, self.quest).quest_instance
        first.local_state = {'progress': 7}
        first.save(update_fields=['local_state'])
        self.new_run()
        self.assertTrue(can_start_template(self.player, self.quest))
        self.assertEqual(build_quest_log(self.player)['active'], [])
        for operation in (
            lambda: info_for_player(self.player, str(first.pk)),
            lambda: choose_for_instance(self.player, str(first.pk), 'finish'),
            lambda: abandon_instance(self.player, str(first.pk)),
        ):
            with self.assertRaises(QuestRuntimeError) as rejected:
                operation()
            self.assertEqual(rejected.exception.code, 'quest_not_found')
        with patch('quests.services.progress.progress_active_instance_for_event') as progress:
            progress_player_quests_for_event(self.player, event_type='quest.mob.killed')
        progress.assert_not_called()
        second = accept_template(self.player, self.quest).quest_instance
        self.assertEqual(second.local_state, {})
        self.move_to(self.run.spawned_world)
        self.assertEqual(info_for_player(self.player, str(first.pk))[0]['id'], first.pk)
        first.refresh_from_db()
        self.assertEqual(first.local_state, {'progress': 7})
        self.assertFalse(can_start_template(self.player, self.quest))

    def test_offers_snoozes_and_cooldowns_are_independent_in_each_run(self):
        self.quest.repeatability_mode = 'cooldown'
        self.quest.repeatability_cooldown_seconds = 3600
        self.quest.save(update_fields=['repeatability_mode', 'repeatability_cooldown_seconds'])
        self.complete()
        old_offer = QuestOfferState.objects.get(player=self.player, world=self.run.spawned_world)
        old_offer.is_visible = True
        old_offer.snoozed_until = timezone.now() + timedelta(days=1)
        old_offer.cooldown_until = timezone.now() + timedelta(days=1)
        old_offer.dismiss_count = 3
        old_offer.save()
        self.assertFalse(_template_available(self.player, self.quest))
        self.new_run()
        self.assertEqual(list_opportunities(self.player, refresh=False), [])
        self.assertTrue(_template_available(self.player, self.quest))
        self.complete()
        current_offer = QuestOfferState.objects.get(player=self.player, world=self.player.world)
        self.assertIsNone(current_offer.snoozed_until)
        self.assertIsNone(current_offer.cooldown_until)
        self.assertEqual(current_offer.dismiss_count, 0)
        self.assertFalse(can_start_template(self.player, self.quest))
        self.assertGreater(build_quest_log(self.player)['repeatable'][0]['repeatability']['remaining_seconds'], 3500)
        old_offer.refresh_from_db()
        self.assertTrue(old_offer.is_visible)
        self.assertEqual(old_offer.dismiss_count, 3)

    def test_base_quests_persist_but_instance_conditions_and_log_stop_at_exit(self):
        instance_attempt = self.complete()
        self.move_to(self.spawn_world)
        base_quest = QuestTemplate.objects.create(
            world=self.world, slug='base', name='Base', status='active', graph=self.quest.graph,
        )
        base_attempt = accept_template(self.player, base_quest).quest_instance
        choose_for_instance(self.player, str(base_attempt.pk), 'finish')
        self.assertEqual(self.states(), (False, False, False))
        self.assertFalse(can_start_template(self.player, self.quest))
        self.assertEqual([q['id'] for q in build_quest_log(self.player)['resolved']], [base_attempt.pk])
        self.new_run()
        self.assertEqual(self.states(base_quest.pk), (True, False, True))
        self.assertFalse(can_start_template(self.player, base_quest))
        self.assertEqual(self.states(), (False, False, False))
        self.assertEqual([q['id'] for q in build_quest_log(self.player)['resolved']], [base_attempt.pk])
        self.assertTrue(QuestInstance.objects.filter(pk=instance_attempt.pk).exists())

    def test_actual_leave_and_reentry_preserve_only_that_retained_run(self):
        self.move_to(self.spawn_world)
        runtime = World.enter_instance(
            player=self.player, transfer_to_id=self.template.config.starting_room_id,
            transfer_from_id=self.player.room_id,
        )
        self.player.refresh_from_db()
        attempt = self.complete()
        ref = runtime.instance_run.ref
        leave_instance(player=self.player)
        self.player.refresh_from_db()
        self.assertEqual(build_quest_log(self.player)['resolved'], [])
        self.assertEqual(self.states(), (False, False, False))
        reentered = World.enter_instance(
            player=self.player, transfer_to_id=self.template.config.starting_room_id,
            transfer_from_id=self.player.room_id, ref=ref,
        )
        self.player.refresh_from_db()
        self.assertEqual(reentered.pk, runtime.pk)
        self.assertEqual(build_quest_log(self.player)['resolved'][0]['id'], attempt.pk)
        self.assertEqual(self.states(), (True, False, True))

    def test_cleanup_deletes_run_offers_without_touching_other_runs_or_base_offers(self):
        self.complete()
        run_offer = QuestOfferState.objects.get(player=self.player, world=self.run.spawned_world)
        self.new_run()
        self.complete()
        current_offer = QuestOfferState.objects.get(player=self.player, world=self.player.world)
        base_quest = QuestTemplate.objects.create(world=self.world, slug='base', name='Base')
        base_offer = QuestOfferState.objects.create(player=self.player, template=base_quest)
        self.run.spawned_world.delete()
        self.assertFalse(QuestOfferState.objects.filter(pk=run_offer.pk).exists())
        self.assertTrue(QuestOfferState.objects.filter(pk=current_offer.pk).exists())
        self.assertTrue(QuestOfferState.objects.filter(pk=base_offer.pk).exists())
        self.assertEqual(self.states(), (True, False, True))

    def test_offer_uniqueness_applies_per_run_and_to_character_wide_base_quests(self):
        self.complete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuestOfferState.objects.create(player=self.player, template=self.quest, world=self.player.world)
        base_quest = QuestTemplate.objects.create(world=self.world, slug='base', name='Base')
        QuestOfferState.objects.create(player=self.player, template=base_quest)
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuestOfferState.objects.create(player=self.player, template=base_quest)

    def test_quest_condition_query_count_does_not_grow_with_other_run_history(self):
        self.complete()
        for index in range(30):
            QuestInstance.objects.create(
                world=self.player.world, template=self.quest, player=self.player,
                status='resolved', resolution='abandoned',
            )
        self.new_run()
        with self.assertNumQueries(1):
            self.assertEqual(self.states(), (False, False, False))
        self.complete()
        with self.assertNumQueries(1):
            self.assertEqual(self.states(), (True, False, True))

    def test_instance_timers_stay_with_run_on_transfer_and_resume_even_when_absent(self):
        self.complete()
        old_offer = QuestOfferState.objects.get(player=self.player, world=self.player.world)
        anchor = old_offer.last_resolved_at
        with patch('spawns.instance_clock.is_time_controlled', return_value=True), patch(
            'spawns.instance_clock.gameplay_now', side_effect=[anchor, anchor + timedelta(days=2)],
        ):
            rebase_character_timers(self.player, self.player.world_id, self.spawn_world.pk)
        old_offer.refresh_from_db()
        self.assertEqual(old_offer.last_resolved_at, anchor)
        self.new_run()
        self.complete()
        current_offer = QuestOfferState.objects.get(player=self.player, world=self.player.world)
        current_anchor = current_offer.last_resolved_at
        _rebase_timers(self.run, timedelta(days=2))
        old_offer.refresh_from_db()
        current_offer.refresh_from_db()
        self.assertEqual(old_offer.last_resolved_at, anchor + timedelta(days=2))
        self.assertEqual(current_offer.last_resolved_at, current_anchor)

    def test_migration_scopes_surviving_history_and_removes_orphaned_instance_offers(self):
        attempt = QuestInstance.objects.create(world=self.player.world, template=self.quest, player=self.player)
        old_offer = QuestOfferState.objects.create(player=self.player, template=self.quest, dismiss_count=2)
        orphan = QuestTemplate.objects.create(world=self.template, slug='orphan', name='Orphan')
        orphan_offer = QuestOfferState.objects.create(player=self.player, template=orphan)
        base = QuestTemplate.objects.create(world=self.world, slug='base', name='Base')
        base_offer = QuestOfferState.objects.create(player=self.player, template=base)
        migration = import_module('quests.migrations.0009_scope_instance_quest_offers')
        migration.scope_existing_offers(apps, SimpleNamespace(connection=connection))
        old_offer.refresh_from_db()
        base_offer.refresh_from_db()
        self.assertEqual(old_offer.world_id, attempt.world_id)
        self.assertEqual(old_offer.dismiss_count, 2)
        self.assertIsNone(base_offer.world_id)
        self.assertFalse(QuestOfferState.objects.filter(pk=orphan_offer.pk).exists())

    def test_http_log_and_mutations_cannot_reach_a_different_run(self):
        first = accept_template(self.player, self.quest).quest_instance
        self.new_run()
        self.client.force_authenticate(self.user)
        headers = {'HTTP_X_PLAYER_ID': str(self.player.pk)}
        response = self.client.get(reverse('game-quest-log'), **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['active'], [])
        for endpoint, data in (
            ('game-quest-instance-choose', {'choice_id': 'finish'}),
            ('game-quest-instance-abandon', {}),
        ):
            response = self.client.post(reverse(endpoint, args=[first.pk]), data, format='json', **headers)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(response.data['code'], 'quest_not_found')
        first.refresh_from_db()
        self.assertEqual(first.status, 'active')

    def test_shared_condition_cache_cannot_carry_instance_completion_to_another_run(self):
        self.complete()
        context = ConditionContext(actor=self.player)
        self.assertTrue(evaluate_condition({'quest_completed': self.quest.pk}, context=context))
        self.new_run()
        self.assertFalse(evaluate_condition({'quest_completed': self.quest.pk}, context=context))

    def test_reverse_migration_retains_latest_offer_when_runs_share_a_template(self):
        self.complete()
        old_offer = QuestOfferState.objects.get(player=self.player, world=self.player.world)
        self.new_run()
        self.complete()
        current_offer = QuestOfferState.objects.get(player=self.player, world=self.player.world)
        base = QuestTemplate.objects.create(world=self.world, slug='base', name='Base')
        base_offer = QuestOfferState.objects.create(player=self.player, template=base)
        migration = import_module('quests.migrations.0009_scope_instance_quest_offers')
        migration.un_scope_offers(apps, SimpleNamespace(connection=connection))
        self.assertFalse(QuestOfferState.objects.filter(pk=old_offer.pk).exists())
        current_offer.refresh_from_db()
        self.assertIsNone(current_offer.world_id)
        self.assertTrue(QuestOfferState.objects.filter(pk=base_offer.pk).exists())
