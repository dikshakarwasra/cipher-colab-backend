from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentUser
from app.models import ActivityLog, Intent, WorkspaceRole
from app.schemas import ActivityPublic
from app.services import require_workspace_role

router = APIRouter(prefix="/workspaces/{workspace_id}/activity", tags=["activity"])


@router.get("", response_model=list[ActivityPublic])
async def list_activity(
    workspace_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor_id: int | None = None,
    file_id: str | None = None,
    intent: Intent | None = None,
) -> list[ActivityPublic]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    query = select(ActivityLog).where(ActivityLog.workspace_id == workspace_id)
    if actor_id is not None:
        query = query.where(ActivityLog.user_id == actor_id)
    if file_id:
        query = query.where(ActivityLog.file_id == file_id)
    if intent:
        query = query.where(ActivityLog.intent == intent)
    result = await db.execute(query.order_by(ActivityLog.created_at.desc()).limit(200))
    return [ActivityPublic.model_validate(item) for item in result.scalars().all()]
