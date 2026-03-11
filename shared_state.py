"""
Shared state management via Redis (Pub/Sub and key-value store).
Used to replace local memory dicts, allowing multiple Uvicorn/Gunicorn
workers to scale horizontally.
"""
import os
import json
import logging
from typing import Dict, List, Optional
import redis.asyncio as aioredis
from fastapi import WebSocket

logger = logging.getLogger("app.state")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# We still need to hold local WebSocket objects for THIS specific server process/worker.
# A websocket connection cannot be serialized into Redis.
# Format: {job_id: [WebSocket, WebSocket, ...]}
local_websockets: Dict[str, List[WebSocket]] = {}

# Create a global Redis connection pool for PubSub
redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

# ── Centralized Redis URL parsing (used by both API and Worker) ──
def parse_redis_url(url: str) -> tuple:
    """Parse redis://host:port/db → (host, port). Single source of truth."""
    try:
        _url_parts = url.replace("redis://", "").split(":")
        host = _url_parts[0]
        port = int(_url_parts[1].split("/")[0])
        return host, port
    except Exception:
        return "localhost", 6379

REDIS_HOST, REDIS_PORT = parse_redis_url(REDIS_URL)

# ARQ connection settings
from arq.connections import RedisSettings
arq_redis_settings = RedisSettings(host=REDIS_HOST, port=REDIS_PORT)
arq_pool = None

async def broadcast(job_id: str, message: dict):
    """
    Publish a message to the Redis Pub/Sub channel for a specific job.
    All Gunicorn workers listening to this channel will receive it and 
    relay it to their locally connected WebSockets.
    """
    try:
        channel_name = f"channel:{job_id}"
        await redis_client.publish(channel_name, json.dumps(message))
    except Exception as e:
        logger.error(f"Redis Broadcast Error for {job_id}: {e}")
