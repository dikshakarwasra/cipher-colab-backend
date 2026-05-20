from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentUser
from app.models import ChatMessage, WorkspaceMember, WorkspaceRole
from app.schemas import ActivityPublic, ChatCreate, ChatPublic
from app.services import log_activity, new_id, require_workspace_role
from app.websocket_manager import manager

router = APIRouter(prefix="/workspaces/{workspace_id}/chat", tags=["chat"])


@router.get("", response_model=list[ChatPublic])
async def list_messages(
    workspace_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ChatPublic]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.workspace_id == workspace_id).order_by(ChatMessage.created_at.desc()).limit(100)
    )
    return [ChatPublic.model_validate(message) for message in reversed(result.scalars().all())]


@router.post("", response_model=ChatPublic, status_code=status.HTTP_201_CREATED)
async def send_message(
    workspace_id: str,
    payload: ChatCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatPublic:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    member = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id)
    )
    current_member = member.scalar_one_or_none()
    if current_member and current_member.muted_chat:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chat is muted by an admin")
    message = ChatMessage(
        id=new_id("msg"),
        workspace_id=workspace_id,
        user_id=user.id,
        username=user.display_name,
        content=payload.content,
        intent=payload.intent,
    )
    db.add(message)
    await db.flush()
    await db.refresh(message)

    activity = await log_activity(db, workspace_id, "chat_message", user.id, payload.intent)
    await db.flush()
    await db.refresh(activity)

    chat_payload = ChatPublic.model_validate(message).model_dump(mode="json")
    activity_payload = ActivityPublic.model_validate(activity).model_dump(mode="json")
    await manager.broadcast(
        workspace_id,
        {"type": "chat_message", "message": chat_payload, "activity": activity_payload},
    )
    return ChatPublic.model_validate(message)
