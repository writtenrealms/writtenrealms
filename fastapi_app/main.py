# fastapi_app/main.py
"""
FastAPI application for WR2.

This provides WebSocket connectivity for the Forge, handling:
- Lobby/user interactions (heartbeat, job dispatch, pub/sub)
- Future: Live gameplay WebSocket
"""
import logging
import os

from fastapi import FastAPI, WebSocket

from .forge_ws import handle_forge_websocket
from .game_ws import handle_game_websocket

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('fastapi_app')

app = FastAPI(title="WR2 FastAPI")

# JWT configuration - SimpleJWT uses JWT_SECRET env var for signing
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"

# Log configuration on startup
@app.on_event("startup")
async def startup_event():
    logger.info(f"FastAPI starting up...")
    logger.info(f"JWT_SECRET configured: {bool(JWT_SECRET)}")
    if JWT_SECRET:
        logger.info(f"JWT_SECRET length: {len(JWT_SECRET)}")
    else:
        logger.error("JWT_SECRET environment variable not set!")


def _extract_token(websocket: WebSocket) -> str | None:
    """Extract JWT token from query params or Authorization header."""
    token = websocket.query_params.get("token")
    if token:
        return token

    auth_header = websocket.headers.get("authorization")
    if not auth_header:
        return None

    parts = auth_header.split(" ", 1)
    if len(parts) != 2:
        return None

    scheme, credentials = parts
    if scheme.lower() not in ("bearer", "jwt"):
        return None

    return credentials.strip()


def _decode_token(token: str) -> dict | None:
    """
    Decode a SimpleJWT access token.

    SimpleJWT tokens are JWTs signed with the Django SECRET_KEY.
    We decode and validate the token, returning the payload if valid.
    """
    import jwt
    from jwt import PyJWTError

    if not JWT_SECRET:
        logger.error("JWT_SECRET not configured")
        return None

    try:
        # SimpleJWT uses HS256 by default with the Django SECRET_KEY
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        logger.debug(f"Successfully decoded token for user_id: {payload.get('user_id')}")
        return payload
    except PyJWTError as e:
        logger.error(f"JWT decode error: {e}")
        logger.error(f"Token (first 20 chars): {token[:20]}...")
        logger.error(f"JWT_SECRET configured: {bool(JWT_SECRET)}")
        return None


async def _get_user_from_token(token: str) -> tuple[int | None, str | None]:
    """
    Get user info from a JWT token.

    Returns (user_id, user_email) or (None, None) if invalid.

    Note: For full Django integration, this could query the database
    to get the user object, but for WebSocket auth we just need the
    user_id from the token payload.
    """
    payload = _decode_token(token)
    if payload is None:
        return None, None

    user_id = payload.get("user_id")
    if user_id is None:
        return None, None

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None, None

    # Email might be in the token payload depending on SimpleJWT config
    # If not, we just use the user_id as identifier for logging
    email = payload.get("email", f"user_{user_id}")

    return user_id, email


def _get_client_ip(websocket: WebSocket) -> str | None:
    """Extract client IP from headers or connection."""
    # Check for X-Forwarded-For header (when behind proxy/load balancer)
    x_forwarded_for = websocket.headers.get("x-forwarded-for")
    if x_forwarded_for:
        # X-Forwarded-For can contain multiple IPs; first is the client
        return x_forwarded_for.split(",")[0].strip()

    # Fall back to direct connection IP
    if websocket.client:
        return websocket.client.host

    return None


@app.websocket("/ws/forge/")
async def forge_websocket(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for the Forge.

    Handles authentication and delegates to the forge WebSocket handler.
    """
    # Extract and validate token
    token = _extract_token(websocket)
    if not token:
        logger.warning("WebSocket connection rejected: missing token")
        await websocket.accept()
        await websocket.close(code=4401)
        return

    user_id, user_email = await _get_user_from_token(token)
    if user_id is None:
        logger.warning(f"WebSocket connection rejected: invalid token")
        await websocket.accept()
        await websocket.close(code=4401)
        return

    # Get client IP for logging
    ip = _get_client_ip(websocket)

    # Handle the WebSocket connection
    await handle_forge_websocket(websocket, user_id, user_email, ip)


@app.websocket("/ws/game/cmd")
async def game_websocket(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for live gameplay.

    Players connect here after entering a world to send game commands.
    """
    await handle_game_websocket(websocket)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
