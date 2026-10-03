from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from pydantic import ValidationError

from quests.manifests import (
    QuestRepeatabilitySpec, apply_quest_manifest, parse_quest_manifest,
    quest_template_to_manifest,
)
from quests.models import QuestOfferState
from quests.services.discovery import _template_available
from quests.services.engine import (
    abandon_instance, accept_template, can_start_template, enter_step,
)
from quests.services.quest_log import build_quest_log
from quests.services.repeatability import next_daily_reset
from tests.test_quest_log import QuestLogTestCase


UTC = timezone.utc
RESET = datetime(2026, 10, 2, 9, tzinfo=UTC)  # 05:00 America/New_York


class TestDailyResetBoundaries(SimpleTestCase):
    def reset(self, completed, reset_at='05:00'):
        return next_daily_reset(
            completed, reset_at=reset_at, timezone_name='America/New_York',
        )

    def test_exact_boundary_and_subsecond_completion(self):
        self.assertEqual(self.reset(RESET - timedelta(microseconds=1)), RESET)
        self.assertEqual(self.reset(RESET), RESET + timedelta(days=1))
        self.assertEqual(self.reset(RESET + timedelta(microseconds=1)), RESET + timedelta(days=1))

    def test_five_am_stays_local_across_spring_and_fall_dst(self):
        spring = datetime(2026, 3, 7, 10, tzinfo=UTC)
        fall = datetime(2026, 10, 31, 9, tzinfo=UTC)
        self.assertEqual(self.reset(spring), datetime(2026, 3, 8, 9, tzinfo=UTC))
        self.assertEqual(self.reset(spring) - spring, timedelta(hours=23))
        self.assertEqual(self.reset(fall), datetime(2026, 11, 1, 10, tzinfo=UTC))
        self.assertEqual(self.reset(fall) - fall, timedelta(hours=25))

    def test_gap_moves_forward_and_fold_uses_only_first_occurrence(self):
        # Missing 02:30 resets at 03:30; ambiguous 01:30 resets at its first occurrence.
        self.assertEqual(
            self.reset(datetime(2026, 3, 8, 7, tzinfo=UTC), '02:30'),
            datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
        )
        self.assertEqual(
            self.reset(datetime(2026, 11, 1, 5, tzinfo=UTC), '01:30'),
            datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        )
        self.assertEqual(
            self.reset(datetime(2026, 11, 1, 5, 30, tzinfo=UTC), '01:30'),
            datetime(2026, 11, 2, 6, 30, tzinfo=UTC),
        )
        self.assertEqual(
            self.reset(datetime(2026, 11, 1, 6, 15, tzinfo=UTC), '01:30'),
            datetime(2026, 11, 2, 6, 30, tzinfo=UTC),
        )

    def test_daily_manifest_requires_strict_local_time_and_timezone(self):
        valid = {'mode': 'daily', 'reset_at': '05:00', 'timezone': 'America/New_York'}
        self.assertEqual(QuestRepeatabilitySpec.model_validate(valid).reset_at, '05:00')
        for values in (
            {'reset_at': ''}, {'reset_at': '5:00'}, {'reset_at': '24:00'},
            {'reset_at': '05:60'}, {'reset_at': '05:00:00'}, {'reset_at': 300},
            {'timezone': ''}, {'timezone': 'New York'}, {'timezone': '../etc/passwd'},
            {'cooldown_seconds': 1}, {'reset_hour': 5}, {'mode': 'always'},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                QuestRepeatabilitySpec.model_validate({**valid, **values})


class TestDailyQuestRuntime(QuestLogTestCase):
    def daily(self, slug='daily'):
        template = self.create_quest(slug, mode='daily')
        template.repeatability_reset_at = '05:00'
        template.repeatability_timezone = 'America/New_York'
        template.save(update_fields=['repeatability_reset_at', 'repeatability_timezone'])
        return template

    def test_eligibility_discovery_and_log_agree_at_reset(self):
        template = self.daily()
        self.create_instance(template, resolved_at=RESET - timedelta(hours=1))
        for now, allowed, remaining in (
            (RESET - timedelta(seconds=1), False, 1),
            (RESET, True, 0),
            (RESET + timedelta(seconds=1), True, 0),
        ):
            with self.subTest(now=now), patch('django.utils.timezone.now', return_value=now):
                self.assertEqual(can_start_template(self.player, template), allowed)
                self.assertEqual(_template_available(self.player, template), allowed)
                payload = build_quest_log(self.player)
                self.assertEqual(payload['wall_time'], now.isoformat())
                repeatability = payload['repeatable'][0]['repeatability']
                self.assertEqual(repeatability['state'], 'ready' if allowed else 'waiting')
                self.assertEqual(repeatability['remaining_seconds'], remaining)
                self.assertEqual(repeatability['ready_at'], RESET.isoformat())
                self.assertEqual(repeatability['reset_at'], '05:00')
                self.assertEqual(repeatability['timezone'], 'America/New_York')

    def test_accept_before_reset_complete_after_reset_consumes_new_day(self):
        template = self.daily()
        with patch('django.utils.timezone.now', return_value=RESET - timedelta(minutes=1)):
            instance = accept_template(self.player, template).quest_instance
        with patch('django.utils.timezone.now', return_value=RESET):
            self.assertFalse(can_start_template(self.player, template))
            self.assertEqual(build_quest_log(self.player)['active'][0]['id'], instance.pk)
            enter_step(instance, step_id='resolved', player=self.player, entry_reason='test')
            self.assertFalse(can_start_template(self.player, template))
            self.assertEqual(build_quest_log(self.player)['repeatable'][0]['repeatability']['remaining_seconds'], 86400)
        with patch('django.utils.timezone.now', return_value=RESET + timedelta(days=1)):
            self.assertTrue(can_start_template(self.player, template))

    def test_abandonment_neither_consumes_daily_nor_replaces_completion(self):
        template = self.daily()
        with patch('django.utils.timezone.now', return_value=RESET):
            instance = accept_template(self.player, template).quest_instance
            abandon_instance(self.player, str(instance.pk))
            self.assertTrue(can_start_template(self.player, template))
            completed = accept_template(self.player, template).quest_instance
            enter_step(completed, step_id='resolved', player=self.player, entry_reason='test')
        # A stale historical abandonment cannot move the true completion anchor.
        self.create_instance(template, resolution='abandoned', resolved_at=RESET + timedelta(days=1))
        with patch('django.utils.timezone.now', return_value=RESET + timedelta(days=1)):
            self.assertTrue(can_start_template(self.player, template))
            payload = build_quest_log(self.player)
            self.assertEqual(payload['repeatable'][0]['id'], completed.pk)
            self.assertEqual(payload['repeatable'][0]['repeatability']['state'], 'ready')

    def test_daily_uses_wall_history_despite_gameplay_anchor_or_clock(self):
        template = self.daily()
        self.create_instance(template, resolved_at=RESET - timedelta(hours=1))
        QuestOfferState.objects.create(
            player=self.player, template=template, last_resolved_at=RESET - timedelta(days=20),
        )
        for gameplay_now in (RESET - timedelta(days=20), RESET + timedelta(days=20)):
            with (
                patch('django.utils.timezone.now', return_value=RESET - timedelta(seconds=1)),
                patch('spawns.instance_clock.gameplay_now', return_value=gameplay_now),
            ):
                self.assertFalse(can_start_template(self.player, template))
                log = build_quest_log(self.player)
                self.assertEqual(log['server_time'], gameplay_now.isoformat())
                self.assertEqual(log['repeatable'][0]['repeatability']['remaining_seconds'], 1)

    def test_daily_history_is_character_specific(self):
        template = self.daily()
        self.create_instance(template, resolved_at=RESET)
        other = self.create_player('Other')
        with patch('django.utils.timezone.now', return_value=RESET):
            self.assertFalse(can_start_template(self.player, template))
            self.assertTrue(can_start_template(other, template))

    def test_live_schedule_edit_recomputes_readiness_without_history_write(self):
        template = self.daily()
        history = self.create_instance(template, resolved_at=RESET - timedelta(hours=1))
        template.repeatability_reset_at = '04:30'
        template.save(update_fields=['repeatability_reset_at'])
        with patch('django.utils.timezone.now', return_value=RESET - timedelta(minutes=30)):
            self.assertTrue(can_start_template(self.player, template))
            self.assertEqual(build_quest_log(self.player)['repeatable'][0]['repeatability']['state'], 'ready')
        history.refresh_from_db()
        self.assertEqual(history.resolved_at, RESET - timedelta(hours=1))

    def test_many_daily_cards_have_bounded_queries_and_no_reset_writes(self):
        for index in range(25):
            template = self.daily(f'daily-{index}')
            self.create_instance(template, resolved_at=RESET - timedelta(hours=1))
        with CaptureQueriesContext(connection) as queries:
            log = build_quest_log(self.player, now=RESET, wall_now=RESET)
        self.assertEqual(len(log['repeatable']), 25)
        self.assertLessEqual(len(queries), 5)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))

    def test_manifest_roundtrip_and_partial_mode_changes(self):
        template = self.daily()
        exported = quest_template_to_manifest(template)
        self.assertEqual(exported['spec']['repeatability'], {
            'mode': 'daily', 'cooldown_seconds': 0, 'reset_at': '05:00',
            'timezone': 'America/New_York',
        })
        parsed = parse_quest_manifest(world=self.world, manifest=exported)
        self.assertEqual(parsed.repeatability_timezone, 'America/New_York')
        for policy in (
            {'mode': 'cooldown', 'cooldown_seconds': 1200},
            {'mode': 'daily', 'reset_at': '05:00', 'timezone': 'America/New_York'},
            {'mode': 'always'},
        ):
            manifest = {'kind': 'quest', 'metadata': {'slug': template.slug}, 'spec': {'repeatability': policy}}
            template = apply_quest_manifest(parse_quest_manifest(world=self.world, manifest=manifest))
            self.assertEqual(template.repeatability_mode, policy['mode'])
            self.assertEqual(template.repeatability_cooldown_seconds, policy.get('cooldown_seconds', 0))
            self.assertEqual(template.repeatability_reset_at, policy.get('reset_at', ''))
            self.assertEqual(template.repeatability_timezone, policy.get('timezone', ''))
