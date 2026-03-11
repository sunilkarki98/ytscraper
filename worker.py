"""
Background Worker (ARQ) for executing heavy crawling jobs.
This process runs strictly independent of the FastAPI web server,
pulling tasks from Redis and processing them sequentially via curl_cffi.
"""
import os
import json
import asyncio
import datetime
import logging
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models import Job, User, Result, JobStatus
from shared_state import broadcast, REDIS_URL, REDIS_HOST, REDIS_PORT
from queue_manager import track_usage
from auth import deduct_credits
from engine.spider import MaxSpeedSpider

logger = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

redis_settings = RedisSettings(host=REDIS_HOST, port=REDIS_PORT)


# Dictionary to hold task cancellation bindings
_running_tasks = {}

import redis.asyncio as aioredis

async def _command_listener(ctx: dict):
    """
    Background loop that listens for "stop" commands pushed to Redis Pub/Sub 
    by the API server, so a scraping job can be cleanly aborted mid-flight.
    """
    worker_redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = worker_redis.pubsub()
    await pubsub.psubscribe("command:*")
    logger.info("📡 Worker listening for Pub/Sub abort commands...")
    
    try:
        async for message in pubsub.listen():
            if message["type"] == "pmessage":
                data = message.get("data", "")
                if data == "stop" or data == b"stop":
                    channel = message.get("channel", "")
                    if isinstance(channel, bytes):
                        channel = channel.decode()
                    job_id = channel.replace("command:", "")
                    logger.info(f"🔔 Received STOP for job {job_id}. Active tasks: {list(_running_tasks.keys())}")
                    if job_id in _running_tasks:
                        logger.warning(f"🛑 Cancelling spider task for job {job_id}...")
                        _running_tasks[job_id].cancel()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Command listener crashed: {e}")
    finally:
        await pubsub.punsubscribe()
        await worker_redis.aclose()


async def startup(ctx: dict):
    """Lifecycle startup for the ARQ worker."""
    logger.info("🚀 ARQ Worker starting up. Initializing Database Engine and PubSub listener...")
    ctx['command_task'] = asyncio.create_task(_command_listener(ctx))


async def shutdown(ctx: dict):
    """Lifecycle shutdown for the ARQ worker."""
    logger.info("🛑 ARQ Worker shutting down.")
    task = ctx.get('command_task')
    if task:
        task.cancel()


async def start_spider_for_job(ctx: dict, job_id: str) -> dict:
    """
    The actual worker task that runs a MaxSpeedSpider.
    Receives events via ctx and pushes results to PostgreSQL and Redis.
    
    Cancellation is handled by ARQ's native abort mechanism:
    - Stop endpoint calls arq_job.abort()
    - ARQ raises CancelledError in this task
    - We catch it and mark the job as STOPPED
    """
    logger.info(f"🕷️ Starting Job {job_id}")
    task = asyncio.current_task()
    _running_tasks[job_id] = task

    async with async_session() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            logger.error(f"Job {job_id} not found in database.")
            return {"error": "not found"}

        # If job was already stopped/completed (e.g. stale retry), skip it
        if job.status in (JobStatus.STOPPED, JobStatus.COMPLETED, JobStatus.ERROR):
            logger.warning(f"⏭️ Job {job_id} already in terminal state '{job.status}', skipping.")
            return {"job": job_id, "status": job.status, "skipped": True}

        result = await db.execute(select(User).where(User.id == job.user_id))
        user = result.scalar_one_or_none()
        if not user:
            logger.error(f"User {job.user_id} not found.")
            return {"error": "user not found"}

        job.status = JobStatus.RUNNING
        job.started_at = datetime.datetime.now(datetime.UTC)
        await db.commit()

        email_count_tracker = [0]
        db_lock = asyncio.Lock()

        # The function injected into the crawler that gets called every time 10 emails are found
        async def push_data_fn(rows):
            async with db_lock:
                for row in rows:
                    email_count_tracker[0] += 1

                    db_result = Result(
                        job_id=job_id,
                        user_id=job.user_id,
                        email=row.get("email", ""),
                        channel_name=row.get("channelName", ""),
                        channel_url=row.get("channelUrl", ""),
                        channel_id=row.get("channelId", ""),
                        subscribers=row.get("subscribers", 0),
                        source=row.get("source", "youtube"),
                        search_keyword=row.get("searchKeyword", ""),
                        instagram=row.get("instagram", ""),
                        twitter=row.get("twitter", ""),
                        tiktok=row.get("tiktok", ""),
                        facebook=row.get("facebook", ""),
                        linkedin=row.get("linkedin", ""),
                        website=row.get("website", ""),
                    )
                    db.add(db_result)

                    # Send to Websocket through Redis PubSub
                    await broadcast(job_id, {
                        "type": "email",
                        "data": row,
                        "total": email_count_tracker[0],
                    })
                try:
                    await db.commit()
                except Exception as e:
                    logger.error(f"DB commit error: {e}")

        # Construct and run spider
        time_budget_seconds = (job.timeout_minutes or 30) * 60
        filters = json.loads(job.filters_json) if job.filters_json else {}
        
        spider = MaxSpeedSpider(
            filters=filters,
            min_subs_filter=job.min_subscribers,
            max_subs_filter=job.max_subscribers,
            time_budget=time_budget_seconds,
            push_data_fn=push_data_fn,
            seed_keyword=job.keyword,
        )

        try:
            await spider.run(
                seed_keyword=job.keyword,
                max_emails=job.max_emails,
            )
            job.status = JobStatus.COMPLETED
            logger.info(f"✅ Job {job_id} Completed gracefully.")
        except asyncio.CancelledError:
            job.status = JobStatus.STOPPED
            logger.info(f"🛑 Job {job_id} was Stopped via abort.")
        except Exception as e:
            job.status = JobStatus.ERROR
            logger.error(f"🔥 Spider error for job {job_id}: {e}")
        finally:
            email_count = email_count_tracker[0]
            job.email_count = email_count
            job.completed_at = datetime.datetime.now(datetime.UTC)
            job.channels_scanned = spider.stats.get("channels_scanned", 0) if spider else 0
            job.stats_json = json.dumps(spider.stats if spider else {})
            
            try:
                await db.commit()

                # Deduct from DB with row-level lock to prevent race condition
                # when multiple concurrent jobs finish for the same user
                if email_count > 0:
                    fresh_user_result = await db.execute(
                        select(User).where(User.id == job.user_id).with_for_update()
                    )
                    fresh_user = fresh_user_result.scalar_one_or_none()
                    if fresh_user:
                        await deduct_credits(db, fresh_user, email_count)
                        await track_usage(db, fresh_user.id, email_count)
            except Exception as e:
                # If we get IllegalStateChangeError or similar, the transaction is already dead.
                logger.error(f"Finally block DB error (safe to ignore if cancelled): {e}")

            try:
                await broadcast(job_id, {
                    "type": "done",
                    "status": job.status,
                    "total": email_count,
                    "stats": spider.stats if spider else {},
                })
            except Exception:
                pass
            
            _running_tasks.pop(job_id, None)

        return {"job": job_id, "status": job.status, "emails": email_count}


class WorkerSettings:
    """
    ARQ Configuration binding.
    Run this file using: `arq worker.WorkerSettings`
    """
    functions = [start_spider_for_job]
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 2                # Max 2 concurrent spiders per worker
    job_timeout = 7200          # 2 hours absolute max
    allow_abort_jobs = True     # Required for arq_job.abort() to work
    max_tries = 1               # NEVER retry cancelled/failed jobs
