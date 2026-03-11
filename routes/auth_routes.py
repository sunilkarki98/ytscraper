"""
Auth routes: /api/auth/me
"""
from fastapi import APIRouter, Depends

from models import User
from auth import get_remaining_credits
from dependencies import get_current_user

router = APIRouter(prefix="/api")


@router.get("/auth/me")
async def get_me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "freeCredits": user.free_credits,
        "paidCredits": user.paid_credits,
        "totalCredits": get_remaining_credits(user),
        "hasDbAddon": user.has_db_addon,
    }
