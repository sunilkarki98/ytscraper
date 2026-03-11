"""
Core scraper routes: /api/start, /api/stop, /api/status, /api/results, /api/export
"""
import io
import csv
import json
import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from database import get_db, async_session
from models import User, Job, Result, JobStatus
from auth import get_remaining_credits
from dependencies import get_current_user, StartRequest
import shared_state

logger = logging.getLogger("app.scraper")

router = APIRouter(prefix="/api")


# ─── Start Job ───────────────────────────────────────────────────────
@router.post("/start")
async def start_job(
    req: StartRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    credits = get_remaining_credits(user)
    if credits <= 0:
        raise HTTPException(status_code=402, detail="No credits remaining. Purchase more to continue.")

    # Single is_free check — used for job limits AND priority scheduling
    is_free_user = user.paid_credits == 0 and not user.has_db_addon

    # ─── Per-user concurrent job limit (anti-abuse) ──────────────
    max_concurrent = 1 if is_free_user else 3
    active_count_result = await db.execute(
        select(func.count()).select_from(Job).where(
            Job.user_id == user.id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
        )
    )
    active_jobs = active_count_result.scalar() or 0
    if active_jobs >= max_concurrent:
        raise HTTPException(
            status_code=429,
            detail=f"You already have {active_jobs} active job(s). Maximum allowed: {max_concurrent}. Wait for current jobs to finish."
        )

    # Cap based on user status
    if is_free_user:
        max_emails = min(req.maxEmails, credits, 500)  # Free tier hard cap
    else:
        max_emails = min(req.maxEmails, credits, 1000)

    filters = {
        'sort_by': req.sortBy if req.sortBy != 'relevance' else '',
        'upload_date': req.uploadDate if req.uploadDate != 'any' else '',
        'min_views': req.minViews,
        'min_duration': req.minDuration,
        'max_duration': req.maxDuration,
        'country': req.country or 'US',
        'language': req.language or 'en',
    }

    job = Job(
        user_id=user.id,
        keyword=req.keyword,
        max_emails=max_emails,
        country=req.country or 'US',
        language=req.language or 'en',
        filters_json=json.dumps(filters),
        min_subscribers=req.minSubscribers,
        max_subscribers=req.maxSubscribers,
        timeout_minutes=min(req.timeoutMinutes, 120),
        status=JobStatus.QUEUED
    )

    if not user.has_db_addon:
        # Standard retention policy for auto-deletion
        job.expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=7)

    db.add(job)
    await db.commit()
    await db.refresh(job)

    if shared_state.arq_pool:
        # ─── Priority Scheduling ─────────────────────────────────────
        # ARQ uses a Redis sorted set (ZSET) ordered by scheduled time.
        # Paid users: immediate execution (_defer_by=0, the default).
        # Free users: 5-second delay so paid jobs always jump ahead.
        defer_seconds = 5 if is_free_user else 0

        await shared_state.arq_pool.enqueue_job(
            'start_spider_for_job', 
            job.id, 
            _job_id=job.id,
            _defer_by=datetime.timedelta(seconds=defer_seconds),
        )
        logger.info(f"Job {job.id} enqueued (priority={'FREE' if is_free_user else 'PAID'}, defer={defer_seconds}s)")
    else:
        logger.error("ARQ pool is not initialized! Check Uvicorn lifespan.")
        raise HTTPException(status_code=500, detail="Task Queue offline")

    return {
        "jobId": job.id,
        "status": "queued",
        "maxEmails": max_emails,
        "creditsAvailable": credits,
    }


# ─── Job Status ──────────────────────────────────────────────────────
@router.get("/status/{job_id}")
async def get_status(
    job_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "jobId": job.id,
        "status": job.status,
        "keyword": job.keyword,
        "total": job.email_count,
        "queuePosition": 0,
        "elapsed": (
            (job.completed_at or datetime.datetime.now(datetime.UTC)) - job.started_at
        ).total_seconds() if job.started_at else 0,
    }


# ─── Paginated Results ──────────────────────────────────────────────
@router.get("/results/{job_id}")
async def get_results(
    job_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    count_result = await db.execute(
        select(func.count(Result.id)).where(Result.job_id == job_id)
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * limit
    results = await db.execute(
        select(Result)
        .where(Result.job_id == job_id)
        .order_by(Result.id)
        .offset(offset)
        .limit(limit)
    )
    rows = results.scalars().all()

    return {
        "results": [
            {**r.to_dict(), "extractedAt": r.extracted_at.isoformat() if r.extracted_at else ""}
            for r in rows
        ],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit if total > 0 else 0,
    }


# ─── Export ──────────────────────────────────────────────────────────
@router.get("/export/{job_id}")
async def export_results(
    job_id: str,
    format: str = "csv",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    count_result = await db.execute(select(func.count(Result.id)).where(Result.job_id == job_id))
    total = count_result.scalar() or 0
    if total == 0:
        raise HTTPException(status_code=400, detail="No results to export")

    fields = ['email', 'channelName', 'channelUrl', 'channelId', 'subscribers',
              'instagram', 'twitter', 'tiktok', 'facebook', 'linkedin', 'website', 'searchKeyword']

    # Uses Result.to_dict() — single source of truth in models.py

    if format == "json":
        async def json_stream():
            yield b"[\n"
            first = True
            result_stream = await db.stream_scalars(
                select(Result).where(Result.job_id == job_id).execution_options(yield_per=1000)
            )
            async for r in result_stream:
                if not first:
                    yield b",\n"
                first = False
                yield json.dumps(r.to_dict()).encode()
            yield b"\n]"

        return StreamingResponse(
            json_stream(),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=emails_{job.keyword}_{total}.json"},
        )
    else:
        async def csv_stream():
            header_buf = io.StringIO()
            writer = csv.DictWriter(header_buf, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            yield header_buf.getvalue().encode('utf-8-sig')

            result_stream = await db.stream_scalars(
                select(Result).where(Result.job_id == job_id).execution_options(yield_per=1000)
            )
            async for r in result_stream:
                chunk_buf = io.StringIO()
                writer = csv.DictWriter(chunk_buf, fieldnames=fields, extrasaction='ignore')
                writer.writerow(r.to_dict())
                yield chunk_buf.getvalue().encode('utf-8')

        return StreamingResponse(
            csv_stream(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=emails_{job.keyword}_{total}.csv"},
        )


# ─── Stop Job ────────────────────────────────────────────────────────
@router.delete("/stop/{job_id}")
async def stop_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 1. Immediately mark as STOPPED in DB so retries skip it
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        job.status = JobStatus.STOPPED
        job.completed_at = datetime.datetime.now(datetime.UTC)
        await db.commit()
        logger.info(f"🛑 Job {job_id} marked as STOPPED in database.")

    # 2. Tell worker to instantly cancel via lightning-fast PubSub
    await shared_state.redis_client.publish(f"command:{job_id}", "stop")
    
    # 3. Tell ARQ to stop any queued task that hasn't started yet
    if shared_state.arq_pool:
        from arq.jobs import Job as ArqJob
        try:
            arq_job = ArqJob(job_id, shared_state.arq_pool)
            await arq_job.abort()
            logger.info(f"🛑 ARQ abort signal sent for job {job_id}.")
        except Exception as e:
            logger.warning(f"ARQ abort failed for {job_id}: {e}")

    return {"status": "stopped", "message": "Job stopped successfully."}

