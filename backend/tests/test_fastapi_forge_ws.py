# backend/tests/test_fastapi_forge_ws.py
"""
Tests for the FastAPI Forge WebSocket implementation.

These tests verify the WebSocket connection lifecycle, authentication,
message handling (heartbeat, job, pub/sub), and integration with the
connection manager.
"""
import asyncio
import json
import os
import unittest
from datetime import datetime
from functools import wraps
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
from starlette.testclient import TestClient
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from builders.models import WorldBuilder
from core.forge_permissions import require_world_admin
from system import tasks as system_tasks
from system.models import SiteControl
from users.models import User
from worlds.models import World
from worlds import tasks as world_tasks
from fastapi_app import forge_ws

# Set up test environment before importing app
os.environ.setdefault('DJANGO_SECRET_KEY', 'test-secret-key-for-testing')

from fastapi_app.main import app, JWT_SECRET, JWT_ALGORITHM
from fastapi_app.forge_ws import (
    ConnectionManager,
    complete_job,
    publish,
    exit_world,
)


def create_test_token(user_id: int, email: str = "test@example.com") -> str:
    """Create a valid JWT token for testing."""
    payload = {
        "user_id": user_id,
        "email": email,
        "token_type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


class TestForgeWebSocketAuth(unittest.TestCase):
    """Tests for WebSocket authentication."""

    def setUp(self):
        self.client = TestClient(app)
        self.valid_token = create_test_token(user_id=1, email="test@example.com")

    def test_connection_without_token_rejected(self):
        """WebSocket connection without token should be rejected."""
        with self.client.websocket_connect("/ws/forge/") as ws:
            # Server accepts then closes with 4401 (unauthorized)
            # The connection should be closed immediately
            message = ws.receive()
            self.assertEqual(message["type"], "websocket.close")
            self.assertEqual(message.get("code"), 4401)

    def test_connection_with_invalid_token_rejected(self):
        """WebSocket connection with invalid token should be rejected."""
        # Create a token with wrong secret
        invalid_token = jwt.encode(
            {"user_id": 1},
            "wrong-secret",
            algorithm=JWT_ALGORITHM
        )
        with self.assertLogs("fastapi_app", level="ERROR") as logs:
            try:
                with self.client.websocket_connect(
                    f"/ws/forge/?token={invalid_token}"
                ) as ws:
                    # Should be closed
                    pass
            except Exception:
                # Expected - connection should be rejected
                pass

        self.assertIn("JWT decode error", "\n".join(logs.output))

    def test_connection_with_valid_token_accepted(self):
        """WebSocket connection with valid token should be accepted."""
        with self.client.websocket_connect(
            f"/ws/forge/?token={self.valid_token}"
        ) as ws:
            # Should receive 'connected' message
            data = ws.receive_json()
            self.assertEqual(data["type"], "connected")

    def test_connection_with_token_in_header(self):
        """WebSocket connection with token in Authorization header."""
        # Note: Starlette TestClient may not support custom headers for WS
        # This test documents the expected behavior
        pass


class TestForgeWebSocketHeartbeat(unittest.TestCase):
    """Tests for heartbeat functionality."""

    def setUp(self):
        self.client = TestClient(app)
        self.token = create_test_token(user_id=1)

    def test_heartbeat_message(self):
        """Client can send heartbeat messages."""
        with self.client.websocket_connect(
            f"/ws/forge/?token={self.token}"
        ) as ws:
            # Receive initial connected message
            data = ws.receive_json()
            self.assertEqual(data["type"], "connected")

            # Send heartbeat
            ws.send_json({"type": "heartbeat"})

            # Heartbeat doesn't generate a response, just updates internal state
            # We can verify by checking the connection manager


def permission_fixtures(target):
    target.author = User.objects.create_user('forge-author@example.com')
    target.builder = User.objects.create_user('forge-builder@example.com')
    target.stranger = User.objects.create_user('forge-stranger@example.com')
    target.staff = User.objects.create_user('forge-staff@example.com', is_staff=True)
    target.rank_zero = User.objects.create_user('forge-rank-zero@example.com')
    target.root = World.objects.create(name='Root', author=target.author)
    target.spawn = World.objects.create(name='Running root', context=target.root)
    target.instance = World.objects.create(
        name='Instance template', instance_of=target.root, author=target.stranger)
    target.instance_spawn = World.objects.create(
        name='Running instance', context=target.instance)
    WorldBuilder.objects.create(world=target.root, user=target.builder, builder_rank=1)
    WorldBuilder.objects.create(world=target.root, user=target.rank_zero, builder_rank=0)
    # Child author/builder status must not replace permission on the root.
    WorldBuilder.objects.create(world=target.instance, user=target.stranger, builder_rank=4)


class TestForgeWebSocketSubscription(TransactionTestCase):
    """Exercise JWT -> gateway -> Django worker -> correlated Redis approval."""

    def setUp(self):
        permission_fixtures(self)
        self.manager = ConnectionManager()
        self.client = TestClient(app)
        patches = [
            patch.object(forge_ws, 'manager', self.manager),
            patch.object(self.manager, 'get_redis', AsyncMock()),
            patch.object(self.manager, 'start_pubsub_listener', AsyncMock()),
            patch.object(self.manager, 'start_heartbeat_checker', AsyncMock()),
            patch.object(forge_ws, 'get_celery_app'),
            patch.object(forge_ws, '_get_sync_redis'),
        ]
        mocks = [item.start() for item in patches]
        for item in patches:
            self.addCleanup(item.stop)
        self.celery = mocks[-2].return_value
        self.celery.send_task.side_effect = self.dispatch_worker
        mocks[-1].return_value.publish.side_effect = self.deliver_approval

    def dispatch_worker(self, name, kwargs, **options):
        self.assertEqual(name, 'system.tasks.authorize_forge_subscription')
        self.assertEqual(options['expires'], forge_ws.SUBSCRIPTION_AUTH_TIMEOUT_SECONDS)
        # This runs on the dispatch thread, like a separate Django worker.
        close_old_connections()
        try:
            system_tasks.authorize_forge_subscription(**kwargs)
        finally:
            close_old_connections()

    def deliver_approval(self, channel, payload):
        self.assertEqual(channel, 'forge:subscription_complete')
        data = json.loads(payload)
        _, future = self.manager.pending_subscriptions[data['client_id']]
        asyncio.run_coroutine_threadsafe(
            self.manager._handle_subscription_complete(data), future.get_loop())

    def assert_subscription(self, user, sub, world_id=None, allowed=False):
        token = create_test_token(user.pk)
        with self.client.websocket_connect(f'/ws/forge/?token={token}') as ws:
            self.assertEqual(ws.receive_json()['type'], 'connected')
            ws.send_json({'type': 'sub', 'sub': sub, 'world_id': world_id,
                          'user_id': self.staff.pk, 'is_staff': True})
            response = ws.receive_json()
            client_id = next(iter(self.manager.active_connections))
            groups = self.manager.client_groups[client_id]
            if allowed:
                self.assertEqual(response['type'], 'subscribed', response)
                expected = f'{sub}-{int(world_id)}' if sub == 'builder.admin' else sub
                self.assertEqual(groups, {expected})
            else:
                self.assertEqual(response['type'], 'error', response)
                self.assertTrue(response['error'])
                self.assertEqual(groups, set())
            self.assertFalse(self.manager.pending_subscriptions)
        self.assertFalse(self.manager.active_connections)

    def test_staff_panel_requires_staff_even_with_forged_payload_roles(self):
        self.assert_subscription(self.stranger, 'staff.panel')
        self.assert_subscription(self.builder, 'staff.panel')
        self.assert_subscription(self.staff, 'staff.panel', allowed=True)

    def test_builder_channels_require_root_world_permission(self):
        for world in (self.root, self.spawn, self.instance, self.instance_spawn):
            for user in (self.stranger, self.rank_zero):
                with self.subTest(world=world.pk, user=user.pk):
                    self.assert_subscription(user, 'builder.admin', world.pk)
            for user in (self.author, self.builder, self.staff):
                with self.subTest(world=world.pk, user=user.pk):
                    self.assert_subscription(user, 'builder.admin', world.pk, allowed=True)

    def test_deleted_inactive_and_revoked_users_are_rejected(self):
        self.staff.is_active = False
        self.staff.save(update_fields=['is_active'])
        self.assert_subscription(self.staff, 'staff.panel')
        WorldBuilder.objects.filter(user=self.builder).delete()
        self.assert_subscription(self.builder, 'builder.admin', self.root.pk)
        deleted_id = self.stranger.pk
        self.stranger.delete()
        self.stranger.pk = deleted_id
        self.assert_subscription(self.stranger, 'staff.panel')

    def test_invalid_and_unknown_channels_fail_closed(self):
        for world_id in (None, True, [], {}, 1.5, 'oops', '9' * 100):
            with self.subTest(world_id=world_id):
                self.assert_subscription(self.staff, 'builder.admin', world_id)
        self.assert_subscription(self.staff, 'builder.admin', 999999999)
        self.assert_subscription(self.staff, f'builder.admin-{self.root.pk}')
        self.assert_subscription(self.staff, 'unknown')

    def test_unsubscribe_removes_the_approved_group(self):
        token = create_test_token(self.staff.pk)
        with self.client.websocket_connect(f'/ws/forge/?token={token}') as ws:
            ws.receive_json()
            ws.send_json({'type': 'sub', 'sub': 'builder.admin',
                          'world_id': f'00{self.root.pk}'})
            self.assertEqual(ws.receive_json()['type'], 'subscribed')
            ws.send_json({'type': 'unsub', 'sub': 'builder.admin',
                          'world_id': f'00{self.root.pk}'})
            # A subsequent response fences processing of the unsubscribe.
            ws.send_json({'type': 'sub', 'sub': 'unknown'})
            self.assertEqual(ws.receive_json()['type'], 'error')
            self.assertEqual(next(iter(self.manager.client_groups.values())), set())


class TestForgeJobPermissions(TestCase):
    @classmethod
    def setUpTestData(cls):
        permission_fixtures(cls)
        cls.site = SiteControl.objects.create(name='prod', maintenance_mode=False)

    def setUp(self):
        redis_patch = patch.object(forge_ws, '_get_sync_redis')
        self.redis = redis_patch.start().return_value
        self.addCleanup(redis_patch.stop)

    def response(self):
        channel, body = self.redis.publish.call_args.args
        self.assertEqual(channel, 'forge:job_complete')
        return json.loads(body)

    def test_world_jobs_reject_unauthorized_users_before_side_effects(self):
        inactive = User.objects.create_user('inactive@example.com', is_active=False)
        for task in (world_tasks.start_world, world_tasks.request_stop,
                     world_tasks.stop_world, world_tasks.kill_world):
            for user_id in (None, 999999999, inactive.pk, self.stranger.pk, self.rank_zero.pk):
                with self.subTest(task=task.name, user_id=user_id), patch('worlds.tasks.WorldSmith') as smith:
                    task(world_id=self.instance_spawn.pk, user_id=user_id, client_id='client')
                    smith.assert_not_called()
                    self.assertEqual(self.response()['status'], 'error')
                    self.assertTrue(self.response()['data']['error'])

    def test_world_jobs_allow_root_author_builder_and_staff(self):
        for task, method in ((world_tasks.start_world, 'start'),
                             (world_tasks.request_stop, 'request_stop'),
                             (world_tasks.stop_world, 'stop'),
                             (world_tasks.kill_world, 'kill')):
            for user in (self.author, self.builder, self.staff):
                for world in (self.root, self.spawn, self.instance, self.instance_spawn):
                    with self.subTest(task=task.name, user=user.pk, world=world.pk), patch('worlds.tasks.WorldSmith') as smith:
                        task(world_id=world.pk, user_id=user.pk, client_id='client')
                        self.assertEqual(smith.call_args.args[0].pk, world.pk)
                        action = getattr(smith.return_value, method)
                        if method == 'start':
                            action.assert_called_once_with(staff_request=user.is_staff)
                        elif method == 'request_stop':
                            action.assert_called_once_with(client_id='client', user_id=user.pk)
                        else:
                            action.assert_called_once_with()
                        if method != 'request_stop':
                            self.assertEqual(self.response()['status'], 'success')

    def test_missing_or_malformed_worlds_return_client_errors(self):
        for world_id in (None, True, {}, 1.5, 'invalid', '9' * 100, 999999999):
            with self.subTest(world_id=world_id), patch('worlds.tasks.WorldSmith') as smith:
                world_tasks.start_world(world_id, user_id=self.staff.pk, client_id='client')
                smith.assert_not_called()
                self.assertEqual(self.response()['status'], 'error')

    def test_staff_jobs_reject_non_staff_and_missing_identity(self):
        inactive = User.objects.create_user('inactive-staff@example.com', is_staff=True, is_active=False)
        for task, kwargs in ((system_tasks.toggle_maintenance_mode, {}),
                             (system_tasks.broadcast, {'message': 'not allowed'})):
            for user_id in (None, 999999999, inactive.pk, self.author.pk, self.builder.pk):
                with self.subTest(task=task.name, user_id=user_id), patch('system.tasks.update_staff_panel') as panel:
                    task(user_id=user_id, client_id='client', **kwargs)
                    self.site.refresh_from_db()
                    self.assertFalse(self.site.maintenance_mode)
                    panel.assert_not_called()
                    self.assertEqual(self.response()['status'], 'error')
                    self.assertNotIn('unavailable', self.response()['data']['error'])

    def test_staff_can_toggle_maintenance(self):
        with patch('system.tasks.update_staff_panel') as panel:
            system_tasks.toggle_maintenance_mode(user_id=self.staff.pk, client_id='client')
        self.site.refresh_from_db()
        self.assertTrue(self.site.maintenance_mode)
        panel.assert_called_once_with()
        self.assertEqual(self.response()['status'], 'success')

    def test_authorized_broadcast_reports_existing_unavailable_feature(self):
        system_tasks.broadcast('hello', user_id=self.staff.pk, client_id='client')
        self.assertEqual(self.response()['data']['error'], 'Broadcasting is currently unavailable.')

    def test_permissions_are_rechecked_at_job_execution(self):
        WorldBuilder.objects.filter(world=self.root, user=self.builder).delete()
        with patch('worlds.tasks.WorldSmith') as smith:
            world_tasks.kill_world(self.spawn.pk, user_id=self.builder.pk, client_id='client')
            smith.assert_not_called()
        self.staff.is_staff = False
        self.staff.save(update_fields=['is_staff'])
        system_tasks.toggle_maintenance_mode(user_id=self.staff.pk, client_id='client')
        self.site.refresh_from_db()
        self.assertFalse(self.site.maintenance_mode)
        self.assertEqual(self.response()['status'], 'error')

    def test_admin_authorization_uses_three_queries_with_many_builders(self):
        users = [User.objects.create_user(f'extra-{i}@example.com') for i in range(30)]
        WorldBuilder.objects.bulk_create([
            WorldBuilder(user=user, world=self.root) for user in users])
        with self.assertNumQueries(3):
            world, user = require_world_admin(self.instance_spawn.pk, self.builder.pk)
        self.assertEqual(world.pk, self.instance_spawn.pk)
        self.assertEqual(user.pk, self.builder.pk)

    def test_delayed_stop_keeps_the_requesting_user(self):
        from config import constants
        from worlds.services import WorldSmith
        self.spawn.lifecycle = constants.WORLD_STATE_RUNNING
        with patch.object(self.spawn, 'set_lifecycle'), patch.object(world_tasks.stop_world, 'apply_async') as queued:
            WorldSmith(self.spawn).request_stop(client_id='client', user_id=self.author.pk)
        queued.assert_called_once_with(
            args=[self.spawn.pk], kwargs={'client_id': 'client', 'user_id': self.author.pk}, countdown=60)


def async_gateway_test(method):
    # IsolatedAsyncioTestCase stores a Context object that Django's parallel
    # runner cannot pickle. Build the event loop only when each test executes.
    @wraps(method)
    def run(self):
        async def exercise():
            await self.setup_gateway()
            await method(self)
        asyncio.run(exercise())
    return run


class TestForgeGatewayPermissions(unittest.TestCase):
    async def setup_gateway(self):
        self.manager = ConnectionManager()
        self.client_id = 'client'
        self.ws = AsyncMock()
        self.manager.active_connections['client'] = self.ws
        self.manager.client_users['client'] = 42
        self.manager.client_groups['client'] = set()
        self.manager.user_clients[42] = {'client'}
        self.manager_patch = patch.object(forge_ws, 'manager', self.manager)
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)
        self.celery_patch = patch.object(forge_ws, 'get_celery_app')
        self.celery = self.celery_patch.start().return_value
        self.addCleanup(self.celery_patch.stop)
        self.dispatched = asyncio.Event()
        loop = asyncio.get_running_loop()
        self.celery.send_task.side_effect = lambda *a, **kw: loop.call_soon_threadsafe(self.dispatched.set)

    async def start_subscription(self):
        task = asyncio.create_task(forge_ws.handle_subscribe(
            {'sub': 'staff.panel', 'user_id': 999}, 'client'))
        await asyncio.wait_for(self.dispatched.wait(), 1)
        return task, self.celery.send_task.call_args.kwargs['kwargs']

    @async_gateway_test
    async def test_privileged_jobs_forward_only_the_authenticated_identity(self):
        for job in ('start_world', 'stop_world', 'kill_world', 'toggle_maintenance_mode', 'broadcast'):
            with self.subTest(job=job):
                await forge_ws.handle_job(
                    {'job': job, 'user_id': 999, 'world_id': 5, 'message': 'hello'},
                    'client', 42, None)
                kwargs = self.celery.send_task.call_args.kwargs['kwargs']
                self.assertEqual(kwargs['user_id'], 42)
                self.assertEqual(kwargs['client_id'], 'client')

    @async_gateway_test
    async def test_no_group_or_publication_before_correlated_approval(self):
        task, kwargs = await self.start_subscription()
        self.assertEqual(kwargs['user_id'], 42)
        self.assertEqual(self.manager.client_groups['client'], set())
        await self.manager._handle_pub({'group': 'staff.panel', 'data': {'private': True}})
        self.ws.send_json.assert_not_called()
        await self.manager._handle_subscription_complete({
            'client_id': 'client', 'request_id': 'wrong', 'status': 'success'})
        await self.manager._handle_subscription_complete({
            'client_id': 'different-client', 'request_id': kwargs['request_id'], 'status': 'success'})
        self.assertFalse(task.done())
        await self.manager._handle_subscription_complete({
            'client_id': 'client', 'request_id': kwargs['request_id'], 'status': 'success'})
        await task
        self.assertEqual(self.manager.client_groups['client'], {'staff.panel'})
        await self.manager._handle_pub({'group': 'staff.panel', 'pub': 'staff.panel', 'data': {'private': True}})
        self.assertEqual(self.ws.send_json.call_args.args[0]['type'], 'pub')
        self.assertFalse(self.manager.pending_subscriptions)

    @async_gateway_test
    async def test_timeout_and_late_reply_do_not_subscribe(self):
        with patch.object(forge_ws, 'SUBSCRIPTION_AUTH_TIMEOUT_SECONDS', 0.02):
            task, kwargs = await self.start_subscription()
            await task
        self.assertIn('timed out', self.ws.send_json.call_args.args[0]['error'])
        await self.manager._handle_subscription_complete({
            'client_id': 'client', 'request_id': kwargs['request_id'], 'status': 'success'})
        self.assertEqual(self.manager.client_groups['client'], set())
        self.assertFalse(self.manager.pending_subscriptions)

    @async_gateway_test
    async def test_worker_dispatch_failure_fails_closed(self):
        self.celery.send_task.side_effect = RuntimeError('broker unavailable')
        with self.assertLogs('forge_ws', level='ERROR'):
            await forge_ws.handle_subscribe({'sub': 'staff.panel'}, 'client')
        self.assertEqual(self.manager.client_groups['client'], set())
        self.assertEqual(self.ws.send_json.call_args.args[0]['type'], 'error')
        self.assertFalse(self.manager.pending_subscriptions)

    @async_gateway_test
    async def test_disconnect_cancels_pending_approval(self):
        task, kwargs = await self.start_subscription()
        await self.manager.disconnect('client')
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.manager._handle_subscription_complete({
            'client_id': 'client', 'request_id': kwargs['request_id'], 'status': 'success'})
        self.assertNotIn('client', self.manager.client_groups)
        self.assertFalse(self.manager.pending_subscriptions)


class TestConnectionManager(unittest.TestCase):
    """Tests for the ConnectionManager class."""

    def setUp(self):
        self.manager = ConnectionManager()

    def test_generate_client_id(self):
        """Client IDs should be unique UUIDs."""
        id1 = self.manager.generate_client_id()
        id2 = self.manager.generate_client_id()
        self.assertNotEqual(id1, id2)
        # Should be valid UUID format
        self.assertEqual(len(id1), 36)  # UUID with dashes

    def test_heartbeat_tracking(self):
        """Manager should track heartbeat timestamps."""
        client_id = "test-client-123"
        self.manager.heartbeats[client_id] = datetime.now()
        self.manager.update_heartbeat(client_id)
        self.assertIn(client_id, self.manager.heartbeats)

    def test_group_management(self):
        """Manager should track group subscriptions."""
        import asyncio

        async def test():
            client_id = "test-client-123"
            self.manager.client_groups[client_id] = set()

            await self.manager.add_to_group(client_id, "test-group")
            self.assertIn("test-group", self.manager.client_groups[client_id])

            await self.manager.remove_from_group(client_id, "test-group")
            self.assertNotIn("test-group", self.manager.client_groups[client_id])

        asyncio.run(test())

    def test_player_client_mapping(self):
        """Manager should track player to client mappings."""
        client_id = "test-client-123"
        player_id = 456

        self.manager.set_player_client(player_id, client_id)
        self.assertEqual(
            self.manager.get_client_id_for_player(player_id),
            client_id
        )

        self.manager.remove_player_client(player_id)
        self.assertIsNone(self.manager.get_client_id_for_player(player_id))


class TestStaticTaskIntegration(unittest.TestCase):
    """Tests for static methods called by Celery tasks."""

    @patch('fastapi_app.forge_ws._get_sync_redis')
    def test_complete_job(self, mock_get_redis):
        """complete_job should publish to Redis."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        complete_job(
            client_id="test-client",
            job="enter_world",
            status="success",
            data={"player_id": 123}
        )

        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        self.assertEqual(call_args[0][0], "forge:job_complete")

        # Verify the published data
        published_data = json.loads(call_args[0][1])
        self.assertEqual(published_data["client_id"], "test-client")
        self.assertEqual(published_data["job"], "enter_world")
        self.assertEqual(published_data["status"], "success")
        self.assertEqual(published_data["data"]["player_id"], 123)

    @patch('fastapi_app.forge_ws._get_sync_redis')
    def test_publish(self, mock_get_redis):
        """publish should send to correct group."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish(
            pub="builder.admin",
            data={"updated": True},
            world_id=123
        )

        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        self.assertEqual(call_args[0][0], "forge:pub")

        # Verify the group name includes world_id
        published_data = json.loads(call_args[0][1])
        self.assertEqual(published_data["group"], "builder.admin-123")
        self.assertEqual(published_data["pub"], "builder.admin")

    @patch('fastapi_app.forge_ws._get_sync_redis')
    def test_publish_without_world_id(self, mock_get_redis):
        """publish without world_id should use pub name as group."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        publish(
            pub="staff.panel",
            data={"message": "test"}
        )

        call_args = mock_redis.publish.call_args
        published_data = json.loads(call_args[0][1])
        self.assertEqual(published_data["group"], "staff.panel")

    @patch('fastapi_app.forge_ws._get_sync_redis')
    @patch('fastapi_app.forge_ws.complete_job')
    def test_exit_world_with_connected_player(self, mock_complete, mock_get_redis):
        """exit_world should notify client if player is connected."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = "test-client-123"
        mock_get_redis.return_value = mock_redis

        exit_world(player_id=456, world_id=789, exit_to="lobby")

        # Should have looked up the client
        mock_redis.get.assert_called_with("forge:connected_player:456")

        # Should have called complete_job
        mock_complete.assert_called_once_with(
            client_id="test-client-123",
            job="exit_world",
            data={
                "player_id": 456,
                "world_id": 789,
                "exit_to": "lobby",
            }
        )

        # Should have deleted the mapping
        mock_redis.delete.assert_called_with("forge:connected_player:456")

    @patch('fastapi_app.forge_ws._get_sync_redis')
    def test_exit_world_without_connected_player(self, mock_get_redis):
        """exit_world should do nothing if player not connected."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        exit_world(player_id=456, world_id=789, exit_to="lobby")

        # Should have looked up the client
        mock_redis.get.assert_called_with("forge:connected_player:456")

        # Should not have deleted anything
        mock_redis.delete.assert_not_called()


class TestHealthEndpoint(unittest.TestCase):
    """Tests for the health check endpoint."""

    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint(self):
        """Health endpoint should return ok status."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
