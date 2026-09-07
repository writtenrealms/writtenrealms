from datetime import timedelta
from unittest.mock import patch

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from quests.models import QuestInstance, QuestOfferState
from quests.services.discovery import _template_available, refresh_player_quests
from quests.services.engine import QuestRuntimeError, accept_template, can_start_template, enter_step
from quests.services.quest_log import build_quest_log
from spawns.instance_clock import rebase_character_timers, simulation_scope
from tests.utils import capture_game_messages
from tests.test_quest_log import QuestLogTestCase
from worlds.instances import create_fresh_instance_run
from worlds.models import World, WorldConfig


class TestInstanceQuestClocks(QuestLogTestCase):
    def setUp(self):
        super().setUp()
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        template = World.objects.new_world(
            name='Paused Adventure', author=self.user, instance_of=self.world,
            is_multiplayer=True,
            config=WorldConfig.objects.create(
                instance_single_player=True, instance_time_control=True,
            ),
        )
        self.run = create_fresh_instance_run(template, leader=self.player)
        self.run.simulation_time = timezone.now() - timedelta(days=2)
        self.run.time_paused = True
        self.run.pause_in_combat = True
        self.run.save(update_fields=['simulation_time', 'time_paused', 'pause_in_combat'])
        self.player.world = self.run.spawned_world
        self.player.room = template.config.starting_room
        self.player.save(update_fields=['world', 'room'])

    def test_quest_cooldown_and_log_use_gameplay_anchor_and_live_template_duration(self):
        template = self.create_quest('thinking-quest', mode='cooldown', cooldown_seconds=120)
        history = self.create_instance(template, resolved_at=timezone.now() - timedelta(days=1))
        anchor = self.run.simulation_time - timedelta(seconds=30)
        QuestOfferState.objects.create(player=self.player, template=template, last_resolved_at=anchor)
        self.assertFalse(can_start_template(self.player, template))
        payload = build_quest_log(self.player)
        self.assertEqual(payload['repeatable'][0]['repeatability']['remaining_seconds'], 90)
        with patch('spawns.instance_clock.timezone.now', return_value=timezone.now() + timedelta(days=10)):
            self.assertFalse(can_start_template(self.player, template))
            self.assertEqual(build_quest_log(self.player)['repeatable'][0]['repeatability']['remaining_seconds'], 90)
        template.repeatability_cooldown_seconds = 20
        template.save(update_fields=['repeatability_cooldown_seconds'])
        self.assertTrue(can_start_template(self.player, template))
        history.refresh_from_db()
        self.assertNotEqual(history.resolved_at, anchor)

    def test_resolution_preserves_audit_time_and_sets_gameplay_cooldown_anchor(self):
        template = self.create_quest('resolution-clock', mode='cooldown', cooldown_seconds=60)
        instance = self.create_instance(template, status='active')
        audit_before = timezone.now()
        with transaction.atomic(), simulation_scope(self.run):
            enter_step(instance, step_id='resolved', player=self.player, entry_reason='test')
        instance.refresh_from_db()
        offer = QuestOfferState.objects.get(player=self.player, template=template)
        self.assertGreaterEqual(instance.resolved_at, audit_before)
        self.assertEqual(offer.last_resolved_at, self.run.simulation_time)
        self.assertFalse(can_start_template(self.player, template))

    def test_opportunity_snooze_and_explicit_deadline_wait_for_instance_time(self):
        template = self.create_quest('snoozed', mode='always')
        offer = QuestOfferState.objects.create(
            player=self.player, template=template,
            snoozed_until=self.run.simulation_time + timedelta(seconds=30),
        )
        self.assertFalse(_template_available(self.player, template))
        offer.snoozed_until = None
        offer.cooldown_until = self.run.simulation_time + timedelta(seconds=30)
        offer.save(update_fields=['snoozed_until', 'cooldown_until'])
        self.assertFalse(_template_available(self.player, template))
        self.run.simulation_time += timedelta(seconds=30)
        self.run.save(update_fields=['simulation_time'])
        self.assertTrue(_template_available(self.player, template))

    def test_reading_opportunities_does_not_create_offer_or_auto_start_quest(self):
        template = self.create_quest('automatic', mode='always')
        template.discovery_policy = {'sources': [{'type': 'auto_start'}]}
        template.save(update_fields=['discovery_policy'])
        self.assertTrue(_template_available(self.player, template))
        result = refresh_player_quests(self.player, allow_auto_start=True)
        self.assertEqual(result.events, [])
        self.assertFalse(QuestOfferState.objects.filter(player=self.player, template=template).exists())
        self.assertFalse(QuestInstance.objects.filter(player=self.player, template=template).exists())

    def test_resume_preserves_quest_and_effect_deadlines_after_long_thinking_time(self):
        from spawns.instance_clock_transitions import resume_clock
        from tests.utils import create_active_effect
        template = self.create_quest('resume-clock', mode='cooldown', cooldown_seconds=120)
        self.create_instance(template, resolved_at=timezone.now())
        offer = QuestOfferState.objects.create(
            player=self.player, template=template,
            last_resolved_at=self.run.simulation_time - timedelta(seconds=30),
            snoozed_until=self.run.simulation_time + timedelta(seconds=45),
        )
        effect = create_active_effect(target=self.player, source=self.player,
                                      payload={'effect': 'crest', 'remaining_rounds': 3})
        effect.next_tick_ts = self.run.simulation_time + timedelta(seconds=2)
        effect.last_tick_ts = self.run.simulation_time
        effect.save(update_fields=['next_tick_ts', 'last_tick_ts'])
        resumed_at = timezone.now()
        with transaction.atomic(), patch('django.utils.timezone.now', return_value=resumed_at):
            resume_clock(self.run)
            self.assertFalse(can_start_template(self.player, template))
            self.assertEqual(build_quest_log(self.player)['repeatable'][0]['repeatability']['remaining_seconds'], 90)
        offer.refresh_from_db()
        effect.refresh_from_db()
        self.assertEqual(offer.snoozed_until, resumed_at + timedelta(seconds=45))
        self.assertEqual(effect.next_tick_ts, resumed_at + timedelta(seconds=2))
        self.assertEqual(effect.last_tick_ts, resumed_at)

    def test_transfer_rebases_remaining_quest_time_without_rewriting_history(self):
        template = self.create_quest('portable', mode='cooldown', cooldown_seconds=120)
        history = self.create_instance(template, resolved_at=timezone.now())
        audit_time = history.resolved_at
        anchor = self.run.simulation_time - timedelta(seconds=30)
        offer = QuestOfferState.objects.create(
            player=self.player, template=template, last_resolved_at=anchor,
            cooldown_until=self.run.simulation_time + timedelta(seconds=45),
            snoozed_until=self.run.simulation_time + timedelta(seconds=60),
        )
        wall_now = timezone.now()
        with patch('spawns.instance_clock.timezone.now', return_value=wall_now):
            rebase_character_timers(self.player, self.run.spawned_world_id, self.spawn_world.pk)
        offer.refresh_from_db()
        history.refresh_from_db()
        self.assertEqual(offer.last_resolved_at, wall_now - timedelta(seconds=30))
        self.assertEqual(offer.cooldown_until, wall_now + timedelta(seconds=45))
        self.assertEqual(offer.snoozed_until, wall_now + timedelta(seconds=60))
        self.assertEqual(history.resolved_at, audit_time)

        self.player.world = self.spawn_world
        self.player.room = self.room
        self.player.save(update_fields=['world', 'room'])
        with patch('django.utils.timezone.now', return_value=wall_now + timedelta(seconds=89)):
            self.assertFalse(can_start_template(self.player, template))
            self.assertEqual(build_quest_log(self.player)['repeatable'][0]['repeatability']['remaining_seconds'], 1)
        with patch('django.utils.timezone.now', return_value=wall_now + timedelta(seconds=90)):
            self.assertTrue(can_start_template(self.player, template))
            self.assertEqual(build_quest_log(self.player)['repeatable'][0]['repeatability']['remaining_seconds'], 0)

    def test_quest_http_mutations_prepare_without_accepting_choosing_or_abandoning(self):
        template = self.create_quest('http-quest', mode='always')
        active = self.create_instance(template, status='active')
        for name, args, payload, command in (
            ('game-quest-opportunity-accept', [template.slug], {}, 'accept'),
            ('game-quest-instance-choose', [active.pk], {'choice_id': 'finish'}, 'choose'),
            ('game-quest-instance-abandon', [active.pk], {}, 'abandon'),
        ):
            with self.subTest(command=command), capture_game_messages():
                response = self.client.post(reverse(name, args=args), payload, format='json', **self.headers)
                self.assertEqual(response.status_code, 202, response.data)
                self.assertTrue(response.data['prepared'])
                self.run.refresh_from_db()
                self.assertEqual(self.run.pending_command['command_type'], 'quest')
                self.assertEqual(self.run.pending_command['payload']['args'][0], command)
                self.assertEqual(self.run.simulation_tick, 0)
                active.refresh_from_db()
                self.assertEqual(active.status, 'active')
                self.assertEqual(active.current_step_id, 'offer')
                self.assertEqual(QuestInstance.objects.filter(player=self.player).count(), 1)

    def test_direct_quest_accept_service_cannot_bypass_preparation(self):
        template = self.create_quest('service-quest', mode='always')
        with self.assertRaises(QuestRuntimeError) as rejected:
            accept_template(self.player, template)
        self.assertEqual(rejected.exception.code, 'instance_turn_required')
        self.assertFalse(QuestInstance.objects.filter(player=self.player, template=template).exists())
