"""
WebSocket route: /ws/{job_id}
"""
import json
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select

from database import async_session
from models import Job, Result
from auth import decode_token
from shared_state import redis_client, local_websockets

logger = logging.getLogger("app.ws")

router = APIRouter()


@router.websocket("/ws/{job_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    job_id: str,
    token: Optional[str] = Query(None),
):
    """WebSocket with JWT auth via query param: ws://host/ws/{job_id}?token=xxx"""
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    payload = decode_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = payload.get("sub")

    # Verify job ownership
    async with async_session() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job or job.user_id != user_id:
            await websocket.close(code=4003, reason="Unauthorized")
            return

    await websocket.accept()

    # Track locally for cleanup 
    if job_id not in local_websockets:
        local_websockets[job_id] = []
    local_websockets[job_id].append(websocket)

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"channel:{job_id}")

    # Listen to Redis and pipe directly to the WebSocket
    async def pubsub_listener():
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if websocket.client_state.name != "DISCONNECTED":
                        await websocket.send_text(data)
        except Exception as e:
            logger.error(f"PubSub listener error: {e}")

    listener_task = asyncio.create_task(pubsub_listener())

    # Send existing results from DB (reconnection support, since RAM states are gone)
    # Uses streaming cursor to avoid loading all results into RAM at once
    async with async_session() as db:
        result_stream = await db.stream_scalars(
            select(Result).where(Result.job_id == job_id).order_by(Result.id).execution_options(yield_per=200)
        )
        idx = 0
        async for r in result_stream:
            try:
                payload = r.to_dict()
                idx += 1
                await websocket.send_json({"type": "email", "data": payload, "total": idx})
            except Exception:
                break

    try:
        while True:
            # We keep the websocket alive waiting for client messages
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("action") == "stop":
                # We can publish a stop command to a Redis 'commands' channel mapped to the job ID
                await redis_client.publish(f"command:{job_id}", "stop")
    except WebSocketDisconnect:
        logger.debug(f"User {user_id} disconnected from Job {job_id}")
    finally:
        listener_task.cancel()
        if pubsub.subscribed:
            await pubsub.unsubscribe()
        
        if job_id in local_websockets:
            try:
                local_websockets[job_id].remove(websocket)
            except ValueError:
                pass
            if not local_websockets[job_id]:
                del local_websockets[job_id]
