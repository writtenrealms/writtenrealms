# fastapi_app/forge_ws.py
"""
FastAPI WebSocket implementation for the Forge.

This module provides WebSocket connectivity for:
- Heartbeat: Client sends periodic heartbeats; server tracks connection health
- Job: Client dispatches async tasks via Celery, receives results
- Pub/Sub: Server publishes events to groups; subscribed clients receive updates

This replaces the Django Channels implementation with a FastAPI-native approach
using Redis for pub/sub and connection state management.
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger('forge_ws')
security_logger = logging.getLogger('security')

# Redis configuration
REDIS_HOST = os.getenv('CHANNEL_REDIS_HOST', '127.0.0.1')
REDIS_PORT = int(os.getenv('CHANNEL_REDIS_PORT', '6379'))

# Celery configuration - for dispatching tasks to Django workers
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'amqp://rabbitmq:5672')

# Heartbeat timeout in minutes
HEARTBEAT_TIMEOUT_MINUTES = 5

# Heartbeat check interval in seconds
HEARTBEAT_CHECK_INTERVAL_SECONDS = 60

# Redis key TTL for player connections (in seconds) - 24 hours
PLAYER_CONNECTION_TTL_SECONDS = 86400

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


class ConnectionManager:
    """
    Manages WebSocket connections for the Forge.

    Tracks:
    - active_connections: Maps client_id to WebSocket connection
    - user_clients: Maps user_id to set of client_ids
    - client_groups: Maps client_id to set of group names
    - connected_players: Maps player_id to client_id for reverse lookup
    - heartbeats: Maps client_id to last heartbeat datetime
    """

    def __init__(self):
        # In-memory state for this process
        self.active_connections: dict[str, WebSocket] = {}
        self.user_clients: dict[int, set[str]] = {}
        self.client_groups: dict[str, set[str]] = {}
        self.client_users: dict[str, int] = {}
        self.connected_players: dict[int, str] = {}
        self.heartbeats: dict[str, datetime] = {}
        self.client_ips: dict[str, str | None] = {}

        # Redis for cross-process pub/sub
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._pubsub_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

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

        # Subscribe to the forge channel for job completions
        await self._pubsub.subscribe("forge:job_complete")
        await self._pubsub.subscribe("forge:pub")

        self._pubsub_task = asyncio.create_task(self._listen_pubsub())

    async def start_heartbeat_checker(self):
        """Start the background heartbeat checker."""
        if self._heartbeat_task is not None:
            return

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self):
        """Periodically check for stale connections."""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL_SECONDS)
                await self.check_heartbeats()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in heartbeat checker: {e}")

    async def _listen_pubsub(self):
        """Listen for Redis pub/sub messages."""
        try:
            async for message in self._pubsub.listen():
                if message['type'] != 'message':
                    continue

                channel = message['channel']
                try:
                    data = json.loads(message['data'])
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in pubsub message: {message['data']}")
                    continue

                if channel == "forge:job_complete":
                    await self._handle_job_complete(data)
                elif channel == "forge:pub":
                    await self._handle_pub(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in pubsub listener: {e}")

    async def _handle_job_complete(self, data: dict):
        """Handle job completion message from Redis."""
        client_id = data.get('client_id')
        if client_id and client_id in self.active_connections:
            ws = self.active_connections[client_id]
            await ws.send_json({
                "type": "job_complete",
                "job": data['job'],
                "job_data": data.get('data', {}),
                "status": data.get('status', 'success')
            })

            # Handle enter_world success - track player to client mapping
            if data['job'] == 'enter_world' and data.get('status') == 'success':
                player_id = data.get('data', {}).get('player_id')
                if player_id:
                    self.connected_players[player_id] = client_id

    async def _handle_pub(self, data: dict):
        """Handle publication message from Redis."""
        group_name = data.get('group')
        pub_data = data.get('data', {})
        pub_name = data.get('pub')

        # Find all clients subscribed to this group
        for client_id, groups in self.client_groups.items():
            if group_name in groups and client_id in self.active_connections:
                ws = self.active_connections[client_id]
                try:
                    await ws.send_json({
                        "type": "pub",
                        "pub": pub_name,
                        "pub_data": pub_data,
                    })
                except Exception as e:
                    logger.error(f"Error sending pub to {client_id}: {e}")

    async def stop_pubsub_listener(self):
        """Stop the Redis pub/sub listener."""
        if self._pubsub_task:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass
            self._pubsub_task = None

        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.close()
            self._pubsub = None

    async def stop_heartbeat_checker(self):
        """Stop the background heartbeat checker."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    def generate_client_id(self) -> str:
        """Generate a unique client ID."""
        return str(uuid.uuid4())

    async def connect(
        self,
        websocket: WebSocket,
        user_id: int,
        ip: str | None = None
    ) -> str:
        """
        Register a new WebSocket connection.

        Returns the client_id for this connection.
        """
        client_id = self.generate_client_id()

        # Register the connection
        self.active_connections[client_id] = websocket
        self.client_users[client_id] = user_id
        self.client_groups[client_id] = set()
        self.client_ips[client_id] = ip
        self.heartbeats[client_id] = datetime.now()

        # Track user's clients
        if user_id not in self.user_clients:
            self.user_clients[user_id] = set()
        self.user_clients[user_id].add(client_id)

        # Ensure background tasks are running
        await self.start_pubsub_listener()
        await self.start_heartbeat_checker()

        return client_id

    async def disconnect(self, client_id: str):
        """Clean up a disconnected client."""
        # Remove from active connections
        self.active_connections.pop(client_id, None)

        # Remove heartbeat
        self.heartbeats.pop(client_id, None)

        # Remove IP tracking
        self.client_ips.pop(client_id, None)

        # Remove from user's client list
        user_id = self.client_users.pop(client_id, None)
        if user_id and user_id in self.user_clients:
            self.user_clients[user_id].discard(client_id)
            if not self.user_clients[user_id]:
                del self.user_clients[user_id]

        # Remove group subscriptions
        self.client_groups.pop(client_id, None)

        # Remove any player mappings
        players_to_remove = [
            pid for pid, cid in self.connected_players.items()
            if cid == client_id
        ]
        for pid in players_to_remove:
            del self.connected_players[pid]

    async def add_to_group(self, client_id: str, group_name: str):
        """Add a client to a group for pub/sub."""
        if client_id in self.client_groups:
            self.client_groups[client_id].add(group_name)

    async def remove_from_group(self, client_id: str, group_name: str):
        """Remove a client from a group."""
        if client_id in self.client_groups:
            self.client_groups[client_id].discard(group_name)

    def update_heartbeat(self, client_id: str):
        """Update the heartbeat timestamp for a client."""
        self.heartbeats[client_id] = datetime.now()

    async def check_heartbeats(self):
        """Check for stale connections and disconnect them."""
        now = datetime.now()
        timeout = timedelta(minutes=HEARTBEAT_TIMEOUT_MINUTES)

        stale_clients = [
            client_id for client_id, last_heartbeat in self.heartbeats.items()
            if now - last_heartbeat > timeout
        ]

        for client_id in stale_clients:
            logger.info(f"Heartbeat timeout for client {client_id}")
            if client_id in self.active_connections:
                ws = self.active_connections[client_id]
                try:
                    await ws.close(code=1000)
                except Exception:
                    pass
            await self.disconnect(client_id)

    async def send_to_client(self, client_id: str, message: dict):
        """Send a message to a specific client."""
        if client_id in self.active_connections:
            ws = self.active_connections[client_id]
            await ws.send_json(message)

    def get_client_id_for_player(self, player_id: int) -> str | None:
        """Get the client_id for a connected player."""
        return self.connected_players.get(player_id)

    def set_player_client(self, player_id: int, client_id: str):
        """Set the player to client mapping."""
        self.connected_players[player_id] = client_id

    def remove_player_client(self, player_id: int):
        """Remove the player to client mapping."""
        self.connected_players.pop(player_id, None)


# Global connection manager instance
manager = ConnectionManager()


# ============================================================================
# Static API for Celery Tasks
# ============================================================================
# These functions are called from Celery tasks to send messages back to clients.
# They publish to Redis, which is then picked up by the appropriate FastAPI
# process handling that client's WebSocket connection.

def _get_sync_redis():
    """Get synchronous Redis connection for use in Celery tasks."""
    import redis
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def complete_job(client_id: str, job: str, status: str = 'success', data: dict | None = None):
    """
    Send job completion to a client. Called from Celery tasks.

    This publishes to Redis, which is picked up by the FastAPI process
    handling the client's WebSocket.
    """
    data = data or {}
    r = _get_sync_redis()
    r.publish("forge:job_complete", json.dumps({
        "client_id": client_id,
        "job": job,
        "status": status,
        "data": data,
    }))


def exit_world(player_id: int, world_id: int, exit_to: str):
    """
    Notify client that a player has exited a world.

    Uses Redis to look up the client_id for the player and sends
    the exit_world job completion.
    """
    r = _get_sync_redis()

    # Look up client_id from Redis
    client_id = r.get(f"forge:connected_player:{player_id}")
    if client_id:
        complete_job(
            client_id=client_id,
            job="exit_world",
            data={
                "player_id": player_id,
                "world_id": world_id,
                "exit_to": exit_to,
            }
        )
        r.delete(f"forge:connected_player:{player_id}")


def publish(pub: str, data: dict, world_id: int | None = None):
    """
    Publish a message to a group. Called from Celery tasks.

    This publishes to Redis, which is picked up by all FastAPI processes
    with clients subscribed to the group.
    """
    if world_id:
        group_name = f"{pub}-{world_id}"
    else:
        group_name = pub

    r = _get_sync_redis()
    r.publish("forge:pub", json.dumps({
        "group": group_name,
        "pub": pub,
        "data": data,
    }))




# ============================================================================
# WebSocket Endpoint Handler
# ============================================================================

async def handle_forge_websocket(
    websocket: WebSocket,
    user_id: int,
    user_email: str,
    ip: str | None = None
):
    """
    Handle a Forge WebSocket connection.

    This is the main entry point for WebSocket connections, called
    after authentication.
    """
    await websocket.accept()

    client_id = await manager.connect(websocket, user_id, ip)

    security_logger.info(f"User {user_email} connected from IP {ip}")

    # Store player->client mapping in Redis for cross-process lookup
    redis = await manager.get_redis()

    # Send connected message
    await websocket.send_json({"type": "connected"})

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get('type')

            logger.info({
                'user_id': user_id,
                'client_id': client_id,
                'data': data,
            })

            if msg_type == 'heartbeat':
                manager.update_heartbeat(client_id)

            elif msg_type == 'job':
                await handle_job(data, client_id, user_id, ip)

            elif msg_type == 'sub':
                await handle_subscribe(data, client_id)

            elif msg_type == 'unsub':
                await handle_unsubscribe(data, client_id)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for client {client_id}: {e}")
    finally:
        # Clean up player mapping from Redis
        players_to_clean = [
            pid for pid, cid in manager.connected_players.items()
            if cid == client_id
        ]
        for pid in players_to_clean:
            await redis.delete(f"forge:connected_player:{pid}")

        await manager.disconnect(client_id)


async def handle_job(data: dict, client_id: str, user_id: int, ip: str | None):
    """
    Handle a job request from the client.

    Uses Celery's send_task() to dispatch tasks by name without needing
    to import Django task modules. This allows FastAPI to run independently
    of Django while still dispatching work to Celery workers.
    """
    celery_app = get_celery_app()
    job = data.get('job')

    if job == 'enter_world':
        celery_app.send_task(
            'spawns.tasks.enter_world',
            kwargs={
                'player_id': data['player_id'],
                'world_id': data['world_id'],
                'client_id': client_id,
                'ip': ip,
            }
        )
        # Store mapping in Redis for cross-process lookup with TTL
        redis = await manager.get_redis()
        await redis.setex(
            f"forge:connected_player:{data['player_id']}",
            PLAYER_CONNECTION_TTL_SECONDS,
            client_id
        )

        # Send acknowledgment that job was queued
        await manager.send_to_client(client_id, {
            "type": "job_queued",
            "job": job
        })

    elif job == 'start_world':
        celery_app.send_task(
            'worlds.tasks.start_world',
            kwargs={
                'world_id': data['world_id'],
                'user_id': user_id,
                'client_id': client_id,
            }
        )
        await manager.send_to_client(client_id, {
            "type": "job_queued",
            "job": job
        })

    elif job == 'stop_world':
        celery_app.send_task(
            'worlds.tasks.stop_world',
            kwargs={
                'world_id': data['world_id'],
                'client_id': client_id,
            }
        )
        await manager.send_to_client(client_id, {
            "type": "job_queued",
            "job": job
        })

    elif job == 'kill_world':
        celery_app.send_task(
            'worlds.tasks.kill_world',
            kwargs={
                'world_id': data['world_id'],
                'client_id': client_id,
            }
        )
        await manager.send_to_client(client_id, {
            "type": "job_queued",
            "job": job
        })

    elif job == 'toggle_maintenance_mode':
        celery_app.send_task(
            'system.tasks.toggle_maintenance_mode',
            kwargs={'client_id': client_id}
        )
        await manager.send_to_client(client_id, {
            "type": "job_queued",
            "job": job
        })

    elif job == 'broadcast':
        celery_app.send_task(
            'system.tasks.broadcast',
            kwargs={
                'message': data['message'],
                'client_id': client_id,
            }
        )
        await manager.send_to_client(client_id, {
            "type": "job_queued",
            "job": job
        })


async def handle_subscribe(data: dict, client_id: str):
    """Handle a subscription request."""
    sub = data.get('sub')

    if sub == 'builder.admin':
        world_id = data.get('world_id')
        await manager.add_to_group(client_id, f"builder.admin-{world_id}")

    elif sub == 'staff.panel':
        await manager.add_to_group(client_id, "staff.panel")


async def handle_unsubscribe(data: dict, client_id: str):
    """Handle an unsubscription request."""
    sub = data.get('sub')

    if sub == 'builder.admin':
        world_id = data.get('world_id')
        await manager.remove_from_group(client_id, f"builder.admin-{world_id}")

    elif sub == 'staff.panel':
        await manager.remove_from_group(client_id, "staff.panel")


# ============================================================================
# Celery Task Stubs
# ============================================================================
# These are synchronous functions that can be called from Celery tasks.

def check_heartbeats():
    """
    Check for stale Forge WebSocket connections.

    This is called from Celery to periodically clean up stale connections.
    It uses Redis to track connection timestamps and sends cleanup messages
    to the appropriate FastAPI processes.

    TODO: Implement proper cross-process heartbeat checking via Redis.
    For now, this is a no-op stub to allow the celery worker to start.
    """
    logger.debug("check_heartbeats called (stub)")
    pass
