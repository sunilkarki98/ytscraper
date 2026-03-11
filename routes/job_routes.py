"""
Job management routes: /api/jobs, /api/queue/status, /api/enterprise-inquiry
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from database import get_db
from models import User, Job, JobStatus
from dependencies import get_current_user

logger = logging.getLogger("app")

router = APIRouter(prefix="/api")


@router.post("/enterprise-inquiry")
async def enterprise_inquiry(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    company = data.get("company", "").strip()
    volume = data.get("volume", "")
    message = data.get("message", "").strip()

    if not name or not email:
        raise HTTPException(status_code=422, detail="Name and email are required.")

    logger.info(f"Enterprise inquiry from {name} <{email}> at {company} — volume: {volume}")
    return {"status": "ok", "message": "Inquiry received"}


@router.get("/jobs")
async def list_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    results = await db.execute(
        select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(50)
    )
    jobs = results.scalars().all()
    return [
        {
            "id": j.id,
            "keyword": j.keyword,
            "status": j.status,
            "total": j.email_count,
            "createdAt": j.created_at.isoformat() if j.created_at else "",
            "expiresAt": j.expires_at.isoformat() if j.expires_at else None,
        }
        for j in jobs
    ]


@router.get("/queue/status")
async def queue_status(db: AsyncSession = Depends(get_db)):
    """Return live job counts from the database instead of dead in-memory state."""
    running_result = await db.execute(
        select(func.count()).select_from(Job).where(Job.status == JobStatus.RUNNING)
    )
    queued_result = await db.execute(
        select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
    )
    return {
        "running": running_result.scalar() or 0,
        "queued": queued_result.scalar() or 0,
    }
