import asyncio
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from fastapi import WebSocketDisconnect

from fastapi_app import game_ws


class FakeGameWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.accepted = False
        self.sent_json = []

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        if not self.messages:
            raise WebSocketDisconnect()

        message = self.messages.pop(0)
        if isinstance(message, BaseException):
            raise message
        return message

    async def send_json(self, message):
        self.sent_json.append(message)


class TestGameConnectionManager(unittest.TestCase):
    def test_disconnect_does_not_clear_newer_connection_for_same_player(self):
        async def run_test():
            manager = game_ws.GameConnectionManager()
            old_socket = MagicMock()
            new_socket = MagicMock()

            await manager.authenticate(old_socket, "player.42")
            new_connection_id = await manager.authenticate(new_socket, "player.42")
            await manager.disconnect(old_socket)

            self.assertIs(manager.active_connections["player.42"], new_socket)
            self.assertEqual(manager.player_connections["player.42"], new_connection_id)
            self.assertNotIn(old_socket, manager.connection_players)
            self.assertNotIn(old_socket, manager.connection_ids)

        asyncio.run(run_test())


class TestGameWebSocketDisconnect(unittest.TestCase):
    def _run_socket(self, websocket, celery_app):
        manager = game_ws.GameConnectionManager()
        manager.start_pubsub_listener = AsyncMock()

        with patch.object(game_ws, "game_manager", manager), patch.object(
            game_ws, "_verify_token", return_value=1
        ), patch.object(game_ws, "get_celery_app", return_value=celery_app):
            asyncio.run(game_ws.handle_game_websocket(websocket))

    def test_system_disconnect_queues_current_world_exit(self):
        celery_app = MagicMock()
        websocket = FakeGameWebSocket(
            [
                {
                    "type": "system.connect",
                    "token": "token",
                    "data": {"player_key": "player.42"},
                },
                {"type": "system.disconnect"},
            ]
        )

        self._run_socket(websocket, celery_app)

        self.assertTrue(websocket.accepted)
        self.assertEqual(
            [message["type"] for message in websocket.sent_json],
            ["system.connect.success", "system.disconnect.success"],
        )
        celery_app.send_task.assert_any_call(
            "spawns.tasks.handle_game_command",
            kwargs={
                "command_type": "state.sync",
                "player_id": 42,
                "player_key": "player.42",
                "payload": {},
                "connection_id": ANY,
            },
        )
        celery_app.send_task.assert_any_call(
            "spawns.tasks.exit_current_world",
            kwargs={"player_id": 42},
        )

    def test_socket_close_queues_current_world_exit(self):
        celery_app = MagicMock()
        websocket = FakeGameWebSocket(
            [
                {
                    "type": "system.connect",
                    "token": "token",
                    "data": {"player_key": "player.42"},
                },
                WebSocketDisconnect(),
            ]
        )

        self._run_socket(websocket, celery_app)

        celery_app.send_task.assert_any_call(
            "spawns.tasks.exit_current_world",
            kwargs={"player_id": 42},
        )
