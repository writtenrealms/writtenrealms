from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.db.models.query import QuerySet
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from spawns.combat_reconciliation import request_reconciliation
from spawns.models import CombatRoomState
from worlds.models import World


class CombatReconciliationQueriesTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        task = self.enterContext(patch("spawns.tasks.reconcile_combat_room"))
        self.enqueue = task.delay
        user = get_user_model().objects.create_user("room-state@example.com", "p")
        world = World.objects.new_world(name="Room state test", author=user)
        self.runtime = world.create_spawn_world()
        self.room = world.config.starting_room

    def _request(self, key):
        request_reconciliation(self.runtime.pk, self.room.pk, [key], observed=True)

    def _state(self):
        return CombatRoomState.objects.get(world=self.runtime, room=self.room)

    def test_existing_state_uses_one_locked_read_and_keeps_durable_work(self):
        state = CombatRoomState.objects.create(
            world=self.runtime, room=self.room,
            changed_actors=["player.10"], dirty_generation=3,
        )

        with CaptureQueriesContext(connection) as queries:
            self._request("player.20")

        reads = [
            query["sql"] for query in queries
            if 'FROM "spawns_combatroomstate"' in query["sql"]
        ]
        self.assertEqual(len(reads), 1)
        self.assertIn("FOR UPDATE", reads[0])
        state.refresh_from_db()
        self.assertEqual(state.changed_actors, ["player.10", "player.20"])
        self.assertEqual(state.dirty_generation, 4)
        self.assertIsNotNone(state.next_run_ts)
        self.assertGreater(state.active_until, state.next_run_ts)
        self.enqueue.assert_called_once_with(state.pk)

        self._request("player.30")
        state.refresh_from_db()
        self.assertEqual(state.dirty_generation, 5)
        self.assertEqual(state.changed_actors, ["player.10", "player.20", "player.30"])
        self.enqueue.assert_called_once_with(state.pk)

    def test_outer_rollback_discards_state_changes_and_enqueue(self):
        state = CombatRoomState.objects.create(world=self.runtime, room=self.room)

        with self.assertRaisesRegex(RuntimeError, "abort"):
            with transaction.atomic():
                self._request("player.10")
                self.enqueue.assert_not_called()
                raise RuntimeError("abort")

        state.refresh_from_db()
        self.assertEqual(state.dirty_generation, 0)
        self.assertEqual(state.changed_actors, [])
        self.assertIsNone(state.next_run_ts)
        self.enqueue.assert_not_called()

    def _concurrent_requests(self):
        gate = Barrier(2)

        def request(key):
            close_old_connections()
            try:
                gate.wait(timeout=5)
                self._request(key)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(request, ["player.10", "player.20"]))

    def _assert_both_requests_recorded(self):
        self.assertEqual(CombatRoomState.objects.count(), 1)
        state = self._state()
        self.assertEqual(state.changed_actors, ["player.10", "player.20"])
        self.assertEqual(state.dirty_generation, 2)
        self.assertIsNotNone(state.next_run_ts)
        self.enqueue.assert_called_once_with(state.pk)

    def test_concurrent_existing_state_updates_preserve_both_generations(self):
        CombatRoomState.objects.create(world=self.runtime, room=self.room)

        self._concurrent_requests()

        self._assert_both_requests_recorded()

    def test_concurrent_missing_state_creation_locks_the_race_winner(self):
        missing = Barrier(2)
        thread = local()
        original_get = QuerySet.get

        def get(queryset, *args, **kwargs):
            if queryset.model is not CombatRoomState or getattr(thread, "checked", False):
                return original_get(queryset, *args, **kwargs)
            thread.checked = True
            try:
                return original_get(queryset, *args, **kwargs)
            except CombatRoomState.DoesNotExist:
                # Both requests must observe absence before either INSERT,
                # exercising get_or_create's IntegrityError retry path.
                missing.wait(timeout=5)
                raise

        with patch.object(QuerySet, "get", get):
            self._concurrent_requests()

        self._assert_both_requests_recorded()
