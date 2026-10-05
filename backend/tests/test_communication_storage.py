from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from time import monotonic, sleep
from unittest.mock import patch

from django.contrib import admin
from django.db import close_old_connections, connection, connections
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from config import constants as api_consts
from spawns.admin import CommunicationMessageAdmin
from spawns.communications import (
    record_answer, record_communication, record_question, reset_communication_session,
    resolve_question,
)
from spawns.models import CommunicationMessage, CommunicationSession, Player
from spawns.serializers import ExtractPlayerSerializer
from spawns.tasks import prune_communication_messages
from tests.base import WorldTestCase
from users.models import User
from worlds.models import World, WorldConfig
from worlds.services import WorldSmith


class TestCommunicationStorage(WorldTestCase):
    def test_new_characters_listen_only_to_ask(self):
        self.assertEqual(self.player.channels, 'ask')

    @override_settings(COMMUNICATION_RETENTION_DAYS=3)
    def test_log_snapshots_identity_and_uses_one_insert(self):
        target = self.create_player('Listener')
        now = timezone.now()
        with patch('spawns.communications.timezone.now', return_value=now):
            with self.assertNumQueries(1):
                message = record_communication(
                    self.player, 'tell', 'A private message', target=target,
                )
        self.assertEqual(message.created_ts, now)
        self.assertEqual(message.expires_at, now + timedelta(days=3))
        self.assertEqual(message.world_id, self.spawn_world.pk)
        self.assertEqual(message.room_id, self.room.pk)
        self.assertEqual(message.sender_type, 'player')
        self.assertEqual(message.sender_id, self.player.pk)
        self.assertEqual(message.sender_user_id, self.user.pk)
        self.assertEqual(message.sender_name, self.player.name)
        self.assertEqual(message.target_id, target.pk)
        self.assertEqual(message.target_user_id, self.user.pk)
        self.assertEqual(message.target_name, target.name)
        self.assertEqual(message.text, 'A private message')

    def test_mob_communication_needs_no_user_or_recipient_lookup(self):
        mob = self.create_mob('A guide')
        with self.assertNumQueries(1):
            message = record_communication(mob, 'say', 'Welcome.')
        self.assertEqual(message.sender_type, 'mob')
        self.assertIsNone(message.sender_user_id)
        self.assertIsNone(message.target_id)

    def test_history_survives_sender_and_world_deletion(self):
        message = record_communication(self.player, 'say', 'Remember this.')
        sender_id, world_id = self.player.pk, self.spawn_world.pk
        self.player.delete()
        self.spawn_world.delete()
        message.refresh_from_db()
        self.assertEqual(message.sender_id, sender_id)
        self.assertEqual(message.world_id, world_id)
        self.assertFalse(CommunicationSession.objects.filter(world_id=world_id).exists())

    @override_settings(COMMUNICATION_RETENTION_DAYS=5)
    def test_retention_setting_changes_expiry_for_new_messages(self):
        message = record_communication(self.player, 'gossip', 'Hello.')
        self.assertEqual(message.expires_at - message.created_ts, timedelta(days=5))

    def test_answers_keep_stable_links_when_short_numbers_are_reused(self):
        question = record_question(self.player, 'How do I play?')
        answer = record_answer(self.player, 'Use help.', question.question_number)
        reset_communication_session(self.spawn_world.pk)
        replacement = record_question(self.player, 'Where am I?')
        self.assertEqual(replacement.question_number, question.question_number)
        self.assertNotEqual(replacement.session, question.session)
        self.assertEqual(answer.in_reply_to_id, question.pk)
        self.assertNotEqual(answer.in_reply_to_id, replacement.pk)

    def test_answer_without_a_session_does_not_create_one(self):
        self.assertIsNone(record_answer(self.player, 'No question exists.'))
        self.assertFalse(CommunicationSession.objects.exists())
        self.assertFalse(CommunicationMessage.objects.exists())

    def test_answer_snapshots_the_question_author_without_loading_the_player(self):
        asker = self.create_player('Mira')
        question = record_question(asker, 'Where is the market?')
        Player.objects.filter(pk=asker.pk).update(name='Renamed')
        with CaptureQueriesContext(connection) as queries:
            answer = record_answer(self.player, 'To the east.', question.question_number)
        self.assertEqual(answer.target_type, 'player')
        self.assertEqual(answer.target_id, asker.pk)
        self.assertEqual(answer.target_user_id, asker.user_id)
        self.assertEqual(answer.target_name, 'Mira')
        self.assertFalse(any('"spawns_player"' in query['sql'] for query in queries))
        answer.refresh_from_db()
        self.assertEqual(answer.target_name, 'Mira')

    def test_answer_without_a_number_uses_latest_current_question(self):
        record_question(self.player, 'First?')
        latest = record_question(self.player, 'Second?')
        answer = record_answer(self.player, 'Here is the answer.')
        self.assertEqual(answer.channel, 'answer')
        self.assertEqual(answer.answer_to_question_number, 2)
        self.assertEqual(answer.in_reply_to_id, latest.pk)
        self.assertEqual(answer.session, latest.session)

    def test_answer_cannot_use_an_expired_or_previous_session_question(self):
        question = record_question(self.player, 'Expired?')
        CommunicationMessage.objects.filter(pk=question.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertIsNone(record_answer(self.player, 'Too late.', 1))
        record_question(self.player, 'Previous session?')
        reset_communication_session(self.spawn_world.pk)
        self.assertIsNone(record_answer(self.player, 'Previous session.', 2))
        self.assertIsNone(record_answer(self.player, 'No question in this session.'))
        self.assertFalse(CommunicationMessage.objects.filter(channel='answer').exists())

    def test_question_numbers_and_latest_resolution_are_per_world(self):
        first = record_question(self.player, 'First?')
        second = record_question(self.player, 'Second?')
        other_world = self.world.create_spawn_world()
        other = self.create_player('Other', world=other_world)
        elsewhere = record_question(other, 'Elsewhere?')
        self.assertEqual((first.question_number, second.question_number), (1, 2))
        self.assertEqual(elsewhere.question_number, 1)
        with self.assertNumQueries(1):
            self.assertEqual(resolve_question(self.spawn_world.pk).pk, second.pk)
        self.assertEqual(resolve_question(self.spawn_world.pk, 1).pk, first.pk)
        self.assertIsNone(resolve_question(other_world.pk, 2))

    def test_expired_questions_are_not_answerable(self):
        question = record_question(self.player, 'Old question?')
        CommunicationMessage.objects.filter(pk=question.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertIsNone(resolve_question(self.spawn_world.pk, 1))
        self.assertIsNone(resolve_question(self.spawn_world.pk))

    def test_world_restart_changes_generation_and_resets_number(self):
        first = record_question(self.player, 'First run?')
        WorldSmith(self.spawn_world).start()
        self.assertIsNone(resolve_question(self.spawn_world.pk, 1))
        second = record_question(self.player, 'New run?')
        self.assertEqual(second.question_number, 1)
        self.assertNotEqual(first.session, second.session)
        self.assertTrue(CommunicationMessage.objects.filter(pk=first.pk).exists())
        WorldSmith(self.spawn_world).stop()
        WorldSmith(self.spawn_world).start()
        third = record_question(self.player, 'Third run?')
        self.assertEqual(third.question_number, 1)
        self.assertNotEqual(second.session, third.session)

    def test_number_survives_reloading_objects_and_ordinary_running_state(self):
        first = record_question(self.player, 'First?')
        self.spawn_world.set_lifecycle(api_consts.WORLD_LIFECYCLE_RUNNING)
        self.player.refresh_from_db()
        second = record_question(self.player, 'Second?')
        self.assertEqual(second.question_number, 2)
        self.assertEqual(second.session, first.session)

    def test_question_allocation_locks_only_communication_state(self):
        reset_communication_session(self.spawn_world.pk)
        with CaptureQueriesContext(connection) as queries:
            record_question(self.player, 'Question?')
        lock_queries = [row['sql'] for row in queries if 'FOR UPDATE' in row['sql']]
        self.assertEqual(len(lock_queries), 1)
        self.assertIn('spawns_communicationsession', lock_queries[0])
        self.assertNotIn('worlds_world', lock_queries[0])

    def test_failed_log_insert_does_not_consume_question_number(self):
        reset_communication_session(self.spawn_world.pk)
        with patch('spawns.communications.record_communication', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                record_question(self.player, 'Lost?')
        self.assertEqual(record_question(self.player, 'Saved?').question_number, 1)

    def test_cleanup_is_bounded_and_keeps_unexpired_messages(self):
        expired = [record_communication(self.player, 'say', str(i)) for i in range(4)]
        fresh = record_communication(self.player, 'say', 'Keep this')
        CommunicationMessage.objects.filter(pk__in=[row.pk for row in expired]).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(prune_communication_messages(batch_size=2), 2)
        self.assertEqual(CommunicationMessage.objects.count(), 3)
        self.assertEqual(prune_communication_messages(batch_size=2), 2)
        self.assertEqual(prune_communication_messages(batch_size=2), 0)
        self.assertTrue(CommunicationMessage.objects.filter(pk=fresh.pk).exists())

    @override_settings(COMMUNICATION_PRUNE_BATCH_SIZE=1)
    def test_cleanup_honors_configured_batch_limit(self):
        messages = [record_communication(self.player, 'say', str(i)) for i in range(2)]
        CommunicationMessage.objects.filter(pk__in=[row.pk for row in messages]).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(prune_communication_messages(), 1)

    def test_extraction_cannot_overwrite_listen_preferences(self):
        self.player.channels = 'ask gossip'
        self.player.save(update_fields=['channels'])
        serializer = ExtractPlayerSerializer(
            self.player, data={'channels': 'chat'}, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.player.refresh_from_db()
        self.assertEqual(self.player.channels, 'ask gossip')

    def test_moderation_admin_is_read_only_and_permission_restricted(self):
        message_admin = CommunicationMessageAdmin(CommunicationMessage, admin.site)
        request = RequestFactory().get('/admin/spawns/communicationmessage/')
        request.user = self.user
        self.assertFalse(message_admin.has_view_permission(request))
        request.user.is_superuser = True
        self.assertTrue(message_admin.has_view_permission(request))
        self.assertFalse(message_admin.has_add_permission(request))
        self.assertFalse(message_admin.has_change_permission(request))
        self.assertFalse(message_admin.has_delete_permission(request))
        self.assertIn('text', message_admin.readonly_fields)


class TestConcurrentQuestionNumbers(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user('ask-concurrency@example.com', 'p')
        root = World.objects.new_world(
            name='Question World', author=self.user,
            config=WorldConfig.objects.create(),
        )
        self.world = root.create_spawn_world()
        self.player = Player.objects.create(name='Asker', user=self.user, world=self.world)

    def test_simultaneous_first_questions_have_unique_contiguous_numbers(self):
        barrier = Barrier(4)

        def ask(index):
            close_old_connections()
            try:
                actor = Player.objects.get(pk=self.player.pk)
                barrier.wait(timeout=10)
                question = record_question(actor, f'Question {index}?')
                return question.question_number, question.session
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(ask, range(4)))
        self.assertEqual(sorted(number for number, _ in results), [1, 2, 3, 4])
        self.assertEqual(len({session for _, session in results}), 1)
        self.assertEqual(CommunicationMessage.objects.count(), 4)
        self.assertEqual(CommunicationSession.objects.get(world=self.world).next_question_number, 5)

    def test_restart_waits_until_resolved_answer_is_logged(self):
        question = record_question(self.player, 'Can this race a restart?')
        answer_ready = Event()
        reset_started = Event()
        allow_answer = Event()
        process_ids = {}

        def delayed_log(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                process_ids['answer'] = cursor.fetchone()[0]
            answer_ready.set()
            if not allow_answer.wait(timeout=10):
                raise TimeoutError('Answer release was not signaled')
            return record_communication(*args, **kwargs)

        def answer():
            close_old_connections()
            try:
                actor = Player.objects.get(pk=self.player.pk)
                return record_answer(actor, 'This completes before the reset.', 1)
            finally:
                connections.close_all()

        def reset():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    process_ids['reset'] = cursor.fetchone()[0]
                reset_started.set()
                return reset_communication_session(self.world.pk)
            finally:
                connections.close_all()

        with patch('spawns.communications.record_communication', side_effect=delayed_log):
            with ThreadPoolExecutor(max_workers=2) as executor:
                pending_answer = executor.submit(answer)
                try:
                    self.assertTrue(answer_ready.wait(timeout=10))
                    pending_reset = executor.submit(reset)
                    self.assertTrue(reset_started.wait(timeout=10))
                    blocked = False
                    deadline = monotonic() + 5
                    while monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                'SELECT %s = ANY(pg_blocking_pids(%s))',
                                [process_ids['answer'], process_ids['reset']],
                            )
                            blocked = cursor.fetchone()[0]
                        if blocked:
                            break
                        sleep(0.01)
                    self.assertTrue(blocked, 'Restart did not wait for the answer transaction')
                finally:
                    allow_answer.set()
                recorded_answer = pending_answer.result(timeout=10)
                new_session = pending_reset.result(timeout=10)

        self.assertEqual(recorded_answer.in_reply_to_id, question.pk)
        self.assertEqual(recorded_answer.session, question.session)
        self.assertNotEqual(new_session.generation, question.session)
        self.assertIsNone(resolve_question(self.world.pk))
