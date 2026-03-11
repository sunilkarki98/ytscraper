"""
Queue utilities — Data retention and usage tracking.
The actual job scheduling is handled by Redis/ARQ (see worker.py).
"""
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Job, Usage

logger = logging.getLogger("queue_manager")


# ─── Configuration ───────────────────────────────────────────────────
RETENTION_DAYS_FREE = 7           # Auto-delete after 7 days for free users


# ─── Data Retention ──────────────────────────────────────────────────

async def cleanup_expired_data(db: AsyncSession):
    """Delete expired jobs and results (7-day retention for free users)."""
    now = datetime.datetime.now(datetime.UTC)

    # Find expired jobs
    expired = await db.execute(
        select(Job).where(
            Job.expires_at.isnot(None),
            Job.expires_at < now
        )
    )
    expired_jobs = expired.scalars().all()
    count = 0
    for job in expired_jobs:
        await db.delete(job)  # CASCADE deletes results too
        count += 1

    if count > 0:
        await db.commit()
        logger.info(f"Cleaned up {count} expired jobs")

    return count


async def retention_worker(get_db_fn):
    """Background task: run cleanup every hour with distributed lock."""
    import asyncio
    from shared_state import redis_client
    LOCK_KEY = "retention_worker_lock"
    LOCK_TTL = 300  # 5 min lock — longer than cleanup should take

    while True:
        try:
            # Only one worker instance should run cleanup at a time
            acquired = await redis_client.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL)
            if acquired:
                async for db in get_db_fn():
                    cleaned = await cleanup_expired_data(db)
                    break
                await redis_client.delete(LOCK_KEY)
        except Exception as e:
            logger.error(f"Retention cleanup error: {e}")
        await asyncio.sleep(3600)  # Every hour


# ─── Usage Tracking ──────────────────────────────────────────────────

async def track_usage(db: AsyncSession, user_id: str, emails_count: int):
    """Track monthly usage for a user."""
    month = datetime.datetime.now(datetime.UTC).strftime("%Y-%m")
    result = await db.execute(
        select(Usage).where(Usage.user_id == user_id, Usage.month == month)
    )
    usage = result.scalar_one_or_none()

    if usage:
        usage.emails_scraped += emails_count
        usage.jobs_run += 1
        usage.credits_used += emails_count
    else:
        usage = Usage(
            user_id=user_id,
            month=month,
            emails_scraped=emails_count,
            jobs_run=1,
            credits_used=emails_count,
        )
        db.add(usage)

    await db.commit()
