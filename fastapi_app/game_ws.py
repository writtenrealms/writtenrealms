# fastapi_app/game_ws.py
"""
FastAPI WebSocket implementation for live gameplay.

This module provides the WebSocket connection for players actively playing
in a world. It handles:
- Player connection/authentication
- Game commands (movement, combat, interaction, etc.)
- Server-to-client notifications (room updates, combat events, etc.)

This replaces the Tornado-based gameplay WebSocket from WR1.
"""
import asyncio
import json
import logging
import os
import uuid

from fastapi import WebSocket, WebSocketDisconnect
import redis.asyncio as aioredis

logger = logging.getLogger('game_ws')

# JWT configuration
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"

# Redis configuration for cross-process game messaging
REDIS_HOST = os.getenv('CHANNEL_REDIS_HOST', '127.0.0.1')
REDIS_PORT = int(os.getenv('CHANNEL_REDIS_PORT', '6379'))
GAME_PUBSUB_CHANNEL = "game:pub"

# Celery configuration - for dispatching commands to Django workers
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'amqp://rabbitmq:5672')

# Lazy-loaded Celery app for task dispatch
_celery_app = None


def get_celery_app():
    """Get or create the Celery app for task dispatch."""
    global _celery_app
    if _celery_app is None:
        from celery import Celery
        _celery_app = Celery(broker=CELERY_BROKER_URL)
        logger.info(f"Celery app initialized with broker: {CELERY_BROKER_URL}")
    return _celery_app


class GameConnectionManager:
    """
    Manages active game WebSocket connections.

    Tracks connected players by their player_key, allowing the game
    engine to send messages to specific players.
    """

    def __init__(self):
        # Map of player_key -> WebSocket connection
        self.active_connections: dict[str, WebSocket] = {}
        # Map of WebSocket -> player_key (reverse lookup)
        self.connection_players: dict[WebSocket, str] = {}
        # Map of WebSocket -> connection_id
        self.connection_ids: dict[WebSocket, str] = {}
        # Map of player_key -> connection_id
        self.player_connections: dict[str, str] = {}

        # Redis pub/sub for cross-process messaging
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._pubsub_task: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket):
        """Accept a new game connection (before authentication)."""
        await websocket.accept()
        logger.info("Game WebSocket accepted")
        await self.start_pubsub_listener()

    async def authenticate(self, websocket: WebSocket, player_key: str) -> str:
        """Register an authenticated player connection."""
        connection_id = str(uuid.uuid4())
        self.active_connections[player_key] = websocket
        self.connection_players[websocket] = player_key
        self.connection_ids[websocket] = connection_id
        self.player_connections[player_key] = connection_id
        logger.info(f"Player {player_key} authenticated")
        return connection_id

    async def disconnect(self, websocket: WebSocket):
        """Clean up a disconnected player."""
        player_key = self.connection_players.pop(websocket, None)
        if player_key:
            self.active_connections.pop(player_key, None)
            self.player_connections.pop(player_key, None)
        self.connection_ids.pop(websocket, None)
        logger.info(f"Player {player_key} disconnected")

    async def send_to_player(self, player_key: str, message: dict):
        """Send a message to a specific player."""
        websocket = self.active_connections.get(player_key)
        if websocket:
            await websocket.send_json(message)

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected players."""
        for player_key in self.active_connections:
            await self.send_to_player(player_key, message)

    async def get_redis(self) -> aioredis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            self._redis = await aioredis.from_url(
                f"redis://{REDIS_HOST}:{REDIS_PORT}",
                decode_responses=True
            )
        return self._redis

    async def start_pubsub_listener(self):
        """Start the Redis pub/sub listener for cross-process messaging."""
        if self._pubsub_task is not None:
            return

        redis = await self.get_redis()
        self._pubsub = redis.pubsub()
        await self._pubsub.subscribe(GAME_PUBSUB_CHANNEL)

        self._pubsub_task = asyncio.create_task(self._listen_pubsub())

    async def _listen_pubsub(self):
        """Listen for Redis pub/sub messages."""
        try:
            async for message in self._pubsub.listen():
                if message['type'] != 'message':
                    continue

                try:
                    data = json.loads(message['data'])
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in pubsub message: {message['data']}")
                    continue

                await self._handle_pub(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in game pubsub listener: {e}")

    async def _handle_pub(self, data: dict):
        """Handle a publication message from Redis."""
        player_key = data.get('player_key')
        message = data.get('message')
        connection_id = data.get('connection_id')

        if not player_key or not message:
            logger.warning(f"Invalid game pub message: {data}")
            return

        if connection_id and self.player_connections.get(player_key) != connection_id:
            logger.debug(f"Ignoring stale message for {player_key}")
            return

        await self.send_to_player(player_key, message)


# Global connection manager for gameplay
game_manager = GameConnectionManager()


# ============================================================================
# Static API for Celery Tasks
# ============================================================================
# These functions are called from Celery tasks to send messages back to players.
# They publish to Redis, which is then picked up by the appropriate FastAPI
# process handling that player's WebSocket connection.

def _get_sync_redis():
    """Get synchronous Redis connection for use in Celery tasks."""
    import redis
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def publish_to_player(player_key: str, message: dict, connection_id: str | None = None):
    """
    Publish a message to a connected player via Redis.
    """
    payload = {
        "player_key": player_key,
        "message": message,
    }
    if connection_id:
        payload["connection_id"] = connection_id

    r = _get_sync_redis()
    r.publish(GAME_PUBSUB_CHANNEL, json.dumps(payload))


def _verify_token(token: str) -> int | None:
    """Verify a JWT token and return the user_id."""
    import jwt
    from jwt import PyJWTError

    if not JWT_SECRET:
        logger.error("JWT_SECRET not configured")
        return None

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("user_id")
    except PyJWTError as e:
        logger.error(f"JWT verification failed: {e}")
        return None


def _parse_player_id(player_key: str) -> int | None:
    """Parse a player_id from a player_key like 'player.123'."""
    if not player_key:
        return None
    if not player_key.startswith("player."):
        return None
    try:
        return int(player_key.split(".", 1)[1])
    except (IndexError, ValueError):
        return None


async def handle_game_websocket(websocket: WebSocket):
    """
    Handle a game WebSocket connection.

    Protocol:
    1. Client connects to /ws/game/cmd
    2. Server accepts connection
    3. Client sends: { type: "system.connect", data: { player_key: "player.123" }, token: "..." }
    4. Server authenticates and responds with system.connect.success or system.connect.error
    5. Server queues an initial state sync and later publishes cmd.state.sync.success
    6. Client sends commands: { type: "cmd.text", text: "look", token: "..." }
    7. Server processes and responds with appropriate messages
    """
    await game_manager.connect(websocket)

    player_key = None
    player_id = None
    connection_id = None
    authenticated = False

    def queue_game_command(command_type: str, payload: dict | None = None):
        if not player_key:
            return
        celery_app = get_celery_app()
        kwargs = {
            "command_type": command_type,
            "player_id": player_id,
            "player_key": player_key,
            "payload": payload or {},
        }
        if connection_id:
            kwargs["connection_id"] = connection_id
        celery_app.send_task('spawns.tasks.handle_game_command', kwargs=kwargs)

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            msg_type = data.get('type')

            logger.info(f"Game WS received: {msg_type} from {player_key or 'unauthenticated'}")

            # Handle system.connect (authentication)
            if msg_type == 'system.connect':
                token = data.get('token')
                if not token:
                    await websocket.send_json({
                        'type': 'system.connect.error',
                        'text': 'Missing authentication token'
                    })
                    continue

                user_id = _verify_token(token)
                if not user_id:
                    await websocket.send_json({
                        'type': 'system.connect.error',
                        'text': 'Invalid authentication token'
                    })
                    continue

                player_key = data.get('data', {}).get('player_key')
                if not player_key:
                    await websocket.send_json({
                        'type': 'system.connect.error',
                        'text': 'Missing player_key'
                    })
                    continue

                # Register the authenticated connection
                connection_id = await game_manager.authenticate(websocket, player_key)
                player_id = _parse_player_id(player_key)
                authenticated = True

                await websocket.send_json({
                    'type': 'system.connect.success',
                    'data': {}
                })

                if not player_id:
                    await websocket.send_json({
                        'type': 'cmd.state.sync.error',
                        'text': f'Invalid player_key: {player_key}'
                    })
                    continue

                # Queue initial state sync as the first command
                queue_game_command('state.sync')
                continue

            # Handle system.disconnect
            if msg_type == 'system.disconnect':
                await websocket.send_json({
                    'type': 'system.disconnect.success',
                    'data': {}
                })
                break

            # All other messages require authentication
            if not authenticated:
                await websocket.send_json({
                    'type': 'error',
                    'text': 'Not authenticated. Send system.connect first.'
                })
                continue

            # Handle state sync command (client-requested)
            if msg_type == 'cmd.state.sync':
                logger.info(f"State sync requested by {player_key}")
                queue_game_command('state.sync')
                continue

            # Handle game commands
            if msg_type == 'cmd.text':
                cmd_text = data.get('text', '')
                logger.info(f"Game command from {player_key}: {cmd_text}")
                queue_game_command('text', {'text': cmd_text})
                continue

            # Handle structured commands
            if msg_type and msg_type.startswith('cmd.'):
                logger.info(f"Structured command from {player_key}: {msg_type}")
                await websocket.send_json({
                    'type': f'{msg_type}.stub',
                    'text': f"[STUB] Received structured command: {msg_type}",
                    'data': data.get('data', {})
                })
                continue

            # Unknown message type
            logger.warning(f"Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        logger.info(f"Game WebSocket disconnected: {player_key}")
    except Exception as e:
        logger.error(f"Game WebSocket error: {e}")
    finally:
        await game_manager.disconnect(websocket)
