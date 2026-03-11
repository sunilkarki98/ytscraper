"""
Dependencies shared across route modules.
Auth helper, request models, etc.
"""
import logging
from typing import Optional

from fastapi import Header, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import User
from auth import (
    decode_token, get_user_by_id, get_remaining_credits,
    create_local_user,
)

logger = logging.getLogger("app")


# ─── Auth Dependency ─────────────────────────────────────────────────
async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract user from Supabase JWT and lazy provision if needed."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization.split(" ", 1)[1]
    payload = decode_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    email = payload.get("email", "")
    name = payload.get("user_metadata", {}).get("full_name", "")

    user = await get_user_by_id(db, user_id)

    # Lazy Provisioning: Supabase gave us a valid JWT, but they don't exist in our DB yet
    if not user:
        user = await create_local_user(db, user_id, email, name)

    return user


# ─── Request Models ──────────────────────────────────────────────────
class StartRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=200)
    maxEmails: int = Field(500, ge=1, le=1000)
    country: str = Field("", max_length=10)
    language: str = Field("en", max_length=10)
    sortBy: str = Field("relevance", max_length=20)
    uploadDate: str = Field("any", max_length=20)
    minSubscribers: int = Field(0, ge=0, le=100_000_000)
    maxSubscribers: int = Field(0, ge=0, le=100_000_000)
    minViews: int = Field(0, ge=0)
    minDuration: int = Field(0, ge=0)
    maxDuration: int = Field(0, ge=0)
    timeoutMinutes: int = Field(30, ge=1, le=120)
