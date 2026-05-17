from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentUser
from app.models import SecurityLog

router = APIRouter(prefix="/security", tags=["security"])


@router.get("/logs")
async def security_logs(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    result = await db.execute(
        select(SecurityLog)
        .where((SecurityLog.user_id == user.id) | (SecurityLog.user_id.is_(None)))
        .order_by(SecurityLog.created_at.desc())
        .limit(100)
    )
    return [
        {
            "id": log.id,
            "event": log.event,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
            "details": log.details,
            "created_at": log.created_at,
        }
        for log in result.scalars().all()
    ]
