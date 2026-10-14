from datetime import timedelta
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from builders.instance_templates import create_instance_template
from quests.models import QuestTemplate
from quests.services.engine import accept_template, abandon_instance, choose_for_instance
from spawns.services import WorldGate
from system.models import SupportEvent
from system.support_events import prune_support_events, record_support_event
from tests.base import WorldTestCase
from worlds.instances import enter_instance, leave_instance
from worlds.models import World
from worlds.tasks import monitor_worlds


class SupportEventTests(WorldTestCase):
    def test_snapshot_cost_and_survival_after_character_and_runtime_deletion(self):
        with self.assertNumQueries(2):
            event = record_support_event('session.login', player=self.player)
        player_id, world_id = self.player.pk, self.spawn_world.pk
        self.player.delete()
        self.spawn_world.delete()
        event.refresh_from_db()
        self.assertEqual(event.player_id, player_id)
        self.assertEqual(event.player_name, 'Joe')
        self.assertEqual(event.world_id, world_id)
        self.assertEqual(event.base_world_id, self.world.pk)
        self.assertEqual(event.details, {})

    def test_rollback_does_not_leave_a_success_event(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            record_support_event('session.login', player=self.player)
            raise RuntimeError('rollback')
        self.assertFalse(SupportEvent.objects.exists())

    def test_details_reject_payloads_and_bound_strings(self):
        with self.assertRaises(ValueError):
            record_support_event('session.login', player=self.player, details={'token': 'private'})
        with self.assertRaises(ValueError):
            record_support_event('quest.started', player=self.player, details={'quest_slug': {'raw': 'data'}})
        event = record_support_event('quest.started', player=self.player, details={'quest_slug': 'a' * 500})
        self.assertEqual(len(event.details['quest_slug']), 160)

    def test_retention_boundary_and_bounded_repeated_batches(self):
        now = timezone.now()
        cutoff = now - timedelta(days=30)
        for _ in range(3):
            SupportEvent.objects.create(kind='session.login', created_ts=cutoff - timedelta(seconds=1))
        boundary = SupportEvent.objects.create(kind='session.login', created_ts=cutoff)
        fresh = SupportEvent.objects.create(kind='session.login', created_ts=now)
        with self.assertLogs('system.support_events', level='WARNING'):
            self.assertEqual(prune_support_events(now=now, batch_size=2, max_batches=1),
                             {'deleted': 2, 'backlog': True})
        self.assertEqual(prune_support_events(now=now, batch_size=2, max_batches=1),
                         {'deleted': 1, 'backlog': False})
        self.assertEqual(set(SupportEvent.objects.values_list('pk', flat=True)), {boundary.pk, fresh.pk})

    def test_cli_filters_limits_and_timezone_validation(self):
        event = record_support_event('session.login', player=self.player)
        record_support_event('session.logout', player=self.player, reason='idle_timeout')
        out = StringIO()
        call_command('support_events', player=self.player.pk, world=self.world.pk,
                     kind='session.login', limit=1, stdout=out)
        self.assertEqual(json.loads(out.getvalue())['id'], event.pk)
        for options in ({'limit': 1001}, {'since': '2026-10-14'}, {'since': 'invalid'}):
            with self.subTest(options=options), self.assertRaises(CommandError):
                call_command('support_events', **options, stdout=StringIO())

    def _enter_private_run(self):
        self.world.is_multiplayer = True
        self.world.save(update_fields=['is_multiplayer'])
        self.spawn_world.is_multiplayer = True
        self.spawn_world.save(update_fields=['is_multiplayer'])
        template = create_instance_template(base_world=self.world, author=self.user, name='Introduction')
        template.config.instance_single_player = True
        template.config.save(update_fields=['instance_single_player'])
        self.player.in_game = True
        self.player.last_action_ts = timezone.now()
        self.player.save(update_fields=['in_game', 'last_action_ts'])
        run = enter_instance(player=self.player, transfer_to=template.config.starting_room,
                             transfer_from=self.room)
        self.player.refresh_from_db()
        return run

    def test_idle_logout_cleanup_history_survives_the_instance(self):
        run = self._enter_private_run()
        runtime_id, run_id, template_id = run.spawned_world_id, run.pk, run.template_world_id
        with patch.object(WorldGate, 'preflight'):
            WorldGate(self.player, self.player.world).enter()
        now = timezone.now()
        self.player.last_action_ts = now - timedelta(minutes=11)
        self.player.save(update_fields=['last_action_ts'])
        with patch('worlds.tasks.timezone.now', return_value=now), patch('worlds.tasks.notify_exit_world'):
            monitor_worlds()
        self.player.refresh_from_db()
        self.assertEqual(self.player.world_id, runtime_id)
        self.assertFalse(self.player.in_game)
        # A duplicate disconnect adds no second support logout event.
        WorldGate(self.player, self.player.world).exit()
        self.assertEqual(SupportEvent.objects.filter(kind='session.logout').count(), 1)
        with patch('worlds.tasks.timezone.now', return_value=now + timedelta(hours=3, seconds=1)):
            monitor_worlds()
        self.player.refresh_from_db()
        self.assertFalse(World.objects.filter(pk=runtime_id).exists())
        self.assertEqual(self.player.world_id, self.spawn_world.pk)
        events = list(SupportEvent.objects.filter(run_id=run_id).order_by('id'))
        self.assertEqual([event.kind for event in events], [
            'instance.entered', 'session.login', 'session.logout', 'instance.cleanup', 'instance.left',
        ])
        self.assertTrue(all(event.world_id == runtime_id and event.template_world_id == template_id
                            and event.base_world_id == self.world.pk for event in events))
        self.assertEqual(events[2].reason, 'idle_timeout')
        self.assertEqual(events[2].details['idle_seconds'], 660)
        self.assertEqual(events[-1].reason, 'idle_expired')
        self.assertEqual(events[-1].details['destination_world_id'], self.spawn_world.pk)

    def test_deliberate_leave_has_a_different_reason(self):
        run = self._enter_private_run()
        from spawns.handlers import dispatch_command
        dispatch_command('leave', player_id=self.player.pk, payload={})
        event = SupportEvent.objects.get(kind='instance.left', run_id=run.pk)
        self.assertEqual(event.reason, 'leave_command')

    def test_quest_history_survives_cleanup_and_records_outcomes(self):
        run = self._enter_private_run()
        template = QuestTemplate.objects.create(
            world=run.template_world, slug='intro', name='Introduction', status='active',
            scope='player', quest_type='quest', discovery_policy={'accept_if': {}},
            graph={'steps': [
                {'id': 'start', 'kind': 'storylet', 'choices': [
                    {'id': 'finish', 'text': 'Finish', 'goto': 'done'}]},
                {'id': 'done', 'kind': 'resolution', 'resolution': 'complete'},
            ]},
        )
        accept_template(self.player, template)
        abandon_instance(self.player, 'intro')
        accept_template(self.player, template)
        choose_for_instance(self.player, 'intro', 'finish')
        from worlds.services import WorldSmith
        WorldSmith(self.player.world).stop(reason='idle_expired')
        history = list(SupportEvent.objects.filter(kind__startswith='quest.').order_by('id'))
        self.assertEqual([(event.kind, event.reason) for event in history], [
            ('quest.started', 'started'), ('quest.resolved', 'abandoned'),
            ('quest.started', 'started'), ('quest.resolved', 'complete'),
        ])
        self.assertTrue(all(event.details['quest_slug'] == 'intro' for event in history))
        self.assertFalse(self.player.quest_instances.exists())
