from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentUser
from app.models import ActivityLog, Intent, JoinRequest, User, Workspace, WorkspaceMember, WorkspaceRole
from app.schemas import (
    IntentSummary,
    InviteRequest,
    JoinRoomRequest,
    JoinRoomResponse,
    MemberPublicWithStatus,
    PresencePublic,
    WorkspaceCreate,
    WorkspacePublic,
)
from app.services import create_workspace_with_seed, get_workspace_role, log_activity, new_id, notify_user, require_workspace_role, workspace_query_for_user
from app.websocket_manager import manager

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspacePublic, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspacePublic:
    workspace = await create_workspace_with_seed(db, user, payload.name, payload.description, payload.template)
    return WorkspacePublic.model_validate(workspace)


@router.get("", response_model=list[WorkspacePublic])
async def list_workspaces(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]) -> list[WorkspacePublic]:
    result = await db.execute(workspace_query_for_user(user.id))
    return [WorkspacePublic.model_validate(item) for item in result.scalars().all()]


@router.get("/{workspace_id}", response_model=WorkspacePublic)
async def get_workspace(
    workspace_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspacePublic:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return WorkspacePublic.model_validate(workspace)


@router.post("/join", response_model=JoinRoomResponse)
async def join_room(
    payload: JoinRoomRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JoinRoomResponse:
    result = await db.execute(select(Workspace).where(Workspace.room_id == payload.room_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")

    existing_role = await get_workspace_role(db, workspace.id, user.id)
    if existing_role:
        return JoinRoomResponse(status="joined", workspace_id=workspace.id, role=existing_role)

    request = JoinRequest(
        id=new_id("join"),
        workspace_id=workspace.id,
        user_id=user.id,
        requested_role=payload.requested_role,
    )
    db.add(request)
    await log_activity(db, workspace.id, "join_requested", user.id, details={"role": payload.requested_role.value})
    await notify_user(db, workspace.owner_id, "Room approval request", f"{user.display_name} wants to join {workspace.name}", "approval", workspace.id)
    return JoinRoomResponse(status="pending_approval", workspace_id=workspace.id)


@router.get("/{workspace_id}/members", response_model=list[MemberPublicWithStatus])
async def list_members(
    workspace_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MemberPublicWithStatus]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    result = await db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
    )
    online_user_ids = set(manager.get_online_user_ids(workspace_id))
    online_users = {u["id"]: u for u in manager.get_online_users(workspace_id)}

    return [
        MemberPublicWithStatus(
            user_id=member.user_id,
            username=joined_user.username,
            display_name=joined_user.display_name,
            role=WorkspaceRole(member.role),
            muted_chat=member.muted_chat,
            is_online=member.user_id in online_user_ids,
            current_intent=online_users.get(member.user_id, {}).get("current_intent"),
        )
        for member, joined_user in result.all()
    ]


@router.get("/{workspace_id}/presence", response_model=list[PresencePublic])
async def workspace_presence(
    workspace_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[PresencePublic]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    online = manager.get_online_users(workspace_id)
    return [
        PresencePublic(
            user_id=int(u["id"]),
            username=u.get("username", ""),
            display_name=u.get("displayName", u.get("username", "")),
            role=u.get("role", "viewer"),
            current_intent=u.get("current_intent"),
        )
        for u in online
    ]


@router.get("/{workspace_id}/intents/summary", response_model=list[IntentSummary])
async def intent_summary(
    workspace_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[IntentSummary]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    result = await db.execute(
        select(ActivityLog.intent, func.count(ActivityLog.id).label("count"))
        .where(ActivityLog.workspace_id == workspace_id, ActivityLog.intent.isnot(None))
        .group_by(ActivityLog.intent)
    )
    return [IntentSummary(intent=row.intent, count=row.count) for row in result.all()]
