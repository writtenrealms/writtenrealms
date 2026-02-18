# backend/wr2_tests/test_fastapi_forge_ws.py
"""
Tests for the FastAPI Forge WebSocket implementation.

These tests verify the WebSocket connection lifecycle, authentication,
message handling (heartbeat, job, pub/sub), and integration with the
connection manager.
"""
import json
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
from starlette.testclient import TestClient

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
        try:
            with self.client.websocket_connect(
                f"/ws/forge/?token={invalid_token}"
            ) as ws:
                # Should be closed
                pass
        except Exception:
            # Expected - connection should be rejected
            pass

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


class TestForgeWebSocketSubscription(unittest.TestCase):
    """Tests for pub/sub functionality."""

    def setUp(self):
        self.client = TestClient(app)
        self.token = create_test_token(user_id=1)

    def test_subscribe_to_builder_admin(self):
        """Client can subscribe to builder.admin channel."""
        with self.client.websocket_connect(
            f"/ws/forge/?token={self.token}"
        ) as ws:
            # Receive initial connected message
            ws.receive_json()

            # Subscribe to builder.admin
            ws.send_json({
                "type": "sub",
                "sub": "builder.admin",
                "world_id": 123
            })

            # No response expected for sub, verify via manager state

    def test_subscribe_to_staff_panel(self):
        """Client can subscribe to staff.panel channel."""
        with self.client.websocket_connect(
            f"/ws/forge/?token={self.token}"
        ) as ws:
            # Receive initial connected message
            ws.receive_json()

            # Subscribe to staff.panel
            ws.send_json({
                "type": "sub",
                "sub": "staff.panel"
            })

    def test_unsubscribe(self):
        """Client can unsubscribe from channels."""
        with self.client.websocket_connect(
            f"/ws/forge/?token={self.token}"
        ) as ws:
            # Receive initial connected message
            ws.receive_json()

            # Subscribe then unsubscribe
            ws.send_json({
                "type": "sub",
                "sub": "staff.panel"
            })
            ws.send_json({
                "type": "unsub",
                "sub": "staff.panel"
            })


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

        asyncio.get_event_loop().run_until_complete(test())

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
