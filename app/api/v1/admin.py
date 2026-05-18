from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentUser
from app.models import JoinRequest, User, WorkspaceFile, WorkspaceMember, WorkspaceRole
from app.schemas import FreezeRequest, InviteRequest, LockRequest, MuteRequest, RoleUpdate
from app.services import log_activity, new_id, notify_user, now_utc, require_workspace_role, set_workspace_frozen

router = APIRouter(prefix="/workspaces/{workspace_id}/admin", tags=["admin"])


@router.get("/join-requests")
async def join_requests(
    workspace_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.admin)
    result = await db.execute(
        select(JoinRequest, User)
        .join(User, User.id == JoinRequest.user_id)
        .where(JoinRequest.workspace_id == workspace_id, JoinRequest.status == "pending")
    )
    return [
        {
            "id": request.id,
            "user_id": request.user_id,
            "display_name": joined_user.display_name,
            "requested_role": request.requested_role,
            "created_at": request.created_at,
        }
        for request, joined_user in result.all()
    ]


@router.post("/join-requests/{request_id}/approve")
async def approve_join(
    workspace_id: str,
    request_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.admin)
    request = await db.get(JoinRequest, request_id)
    if not request or request.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Join request not found")
    request.status = "approved"
    request.decided_at = now_utc()
    db.add(WorkspaceMember(workspace_id=workspace_id, user_id=request.user_id, role=request.requested_role))
    await notify_user(db, request.user_id, "Access granted", "An admin approved your workspace request", "access_granted", workspace_id)
    await log_activity(db, workspace_id, "join_approved", user.id, details={"requestId": request_id})
    return {"success": True}


@router.post("/join-requests/{request_id}/reject")
async def reject_join(
    workspace_id: str,
    request_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.admin)
    request = await db.get(JoinRequest, request_id)
    if not request or request.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Join request not found")
    request.status = "rejected"
    request.decided_at = now_utc()
    await notify_user(db, request.user_id, "Access denied", "An admin rejected your workspace request", "access_denied", workspace_id)
    await log_activity(db, workspace_id, "join_rejected", user.id, details={"requestId": request_id})
    return {"success": True}


@router.post("/invite")
async def direct_invite(
    workspace_id: str,
    payload: InviteRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.admin)
    result = await db.execute(select(User).where(User.username == payload.username))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{payload.username}' not found")
    existing = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == target.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already a member")
    db.add(WorkspaceMember(workspace_id=workspace_id, user_id=target.id, role=payload.role))
    await notify_user(db, target.id, "Workspace invitation", f"{user.display_name} added you to a workspace", "invitation", workspace_id)
    await log_activity(db, workspace_id, "member_invited", user.id, details={"username": payload.username, "role": payload.role.value})
    return {"success": True}


@router.post("/roles")
async def change_role(
    workspace_id: str,
    payload: RoleUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.admin)
    await db.execute(
        update(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == payload.user_id)
        .values(role=payload.role)
    )
    await log_activity(db, workspace_id, "role_changed", user.id, details={"userId": payload.user_id, "role": payload.role.value})
    return {"success": True}


@router.post("/members/{target_user_id}/mute")
async def mute_member(
    workspace_id: str,
    target_user_id: int,
    payload: MuteRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.admin)
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == target_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    member.muted_chat = payload.muted
    action = "member_muted" if payload.muted else "member_unmuted"
    await log_activity(db, workspace_id, action, user.id, details={"targetUserId": target_user_id})
    return {"success": True}


@router.delete("/members/{target_user_id}")
async def remove_member(
    workspace_id: str,
    target_user_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.admin)
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == target_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    await db.delete(member)
    await log_activity(db, workspace_id, "member_removed", user.id, details={"targetUserId": target_user_id})
    return {"success": True}


@router.post("/files/{file_id}/lock")
async def lock_file(
    workspace_id: str,
    file_id: str,
    payload: LockRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.admin)
    file = await db.get(WorkspaceFile, file_id)
    if not file or file.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    file.locked_by = user.id if payload.locked else None
    await log_activity(db, workspace_id, "file_locked" if payload.locked else "file_unlocked", user.id, file_id=file.id)
    return {"success": True}


@router.post("/freeze")
async def freeze_workspace(
    workspace_id: str,
    payload: FreezeRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.admin)
    await set_workspace_frozen(db, workspace_id, payload.frozen)
    await log_activity(db, workspace_id, "workspace_frozen" if payload.frozen else "workspace_unfrozen", user.id)
    return {"success": True}
