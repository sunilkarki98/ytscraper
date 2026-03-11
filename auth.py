"""
Auth system — Supabase JWT implementation.
Backend validates Supabase JWTs securely using pyjwt.
"""
import os
import logging
import jwt
from typing import Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from models import User

logger = logging.getLogger("auth")

# Secret key for JWT — must match your Supabase JWT secret
# Accepts SUPABASE_JWT_SECRET (preferred) or JWT_SECRET (backward compat)
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("JWT_SECRET")
if not SUPABASE_JWT_SECRET:
    raise RuntimeError(
        "FATAL: No JWT secret configured. "
        "Set SUPABASE_JWT_SECRET or JWT_SECRET environment variable. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
ALGORITHM = "HS256"

def decode_token(token: str) -> Optional[dict]:
    """Decode and validate a Supabase JWT token."""
    try:
        # Supabase sets the 'aud' (audience) string to 'authenticated'
        return jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=[ALGORITHM], audience="authenticated")
    except Exception as e:
        logger.warning(f"JWT Decode error: {e}")
        return None


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """Find user by email."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[User]:
    """Find user by ID."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_local_user(db: AsyncSession, user_id: str, email: str, name: str = "") -> User:
    """Lazy provision a new user from Supabase with 500 free credits."""
    user = User(
        id=user_id, # Must match Supabase unique UUID
        email=email.lower().strip(),
        password_hash="", # Passwords handled by Supabase
        name=name,
        free_credits=500,
        paid_credits=0,
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError:
        # A concurrent request beat us to creating this lazy user.
        await db.rollback()
        user = await get_user_by_id(db, user_id)
        if not user:
            raise Exception("Failed to fetch user during concurrent provision.")
            
    return user


def get_remaining_credits(user: User) -> int:
    """Total remaining credits (free + paid)."""
    return user.free_credits + user.paid_credits


async def deduct_credits(db: AsyncSession, user: User, count: int) -> bool:
    """Deduct credits — free first, then paid. Returns False if insufficient."""
    total = user.free_credits + user.paid_credits
    if total < count:
        return False

    # Use free credits first
    if user.free_credits >= count:
        user.free_credits -= count
    else:
        remaining = count - user.free_credits
        user.free_credits = 0
        user.paid_credits -= remaining

    await db.commit()
    return True
