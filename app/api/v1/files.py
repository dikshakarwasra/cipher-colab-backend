from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentUser
from app.models import CodeChange, Intent, Workspace, WorkspaceFile, WorkspaceRole
from app.schemas import FileCreate, FilePublic, FileUpdate, VersionPublic
from app.services import create_version, log_activity, new_id, now_utc, require_workspace_role
from app.websocket_manager import manager

router = APIRouter(prefix="/workspaces/{workspace_id}/files", tags=["files"])


@router.get("", response_model=list[FilePublic])
async def list_files(
    workspace_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[FilePublic]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    result = await db.execute(select(WorkspaceFile).where(WorkspaceFile.workspace_id == workspace_id).order_by(WorkspaceFile.path))
    return [FilePublic.model_validate(file) for file in result.scalars().all()]


@router.post("", response_model=FilePublic, status_code=status.HTTP_201_CREATED)
async def create_file(
    workspace_id: str,
    payload: FileCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FilePublic:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.editor)
    existing = await db.execute(
        select(WorkspaceFile).where(
            WorkspaceFile.workspace_id == workspace_id,
            WorkspaceFile.path == payload.path,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "A file with this path already exists")

    file = WorkspaceFile(
        id=new_id("fil"),
        workspace_id=workspace_id,
        name=payload.name,
        path=payload.path,
        language=payload.language,
        content=payload.content,
        created_by=user.id,
    )
    db.add(file)
    await db.flush()
    await db.refresh(file)

    await manager.broadcast(workspace_id, {"type": "file_created", "file": FilePublic.model_validate(file).model_dump(mode="json")})

    await create_version(db, file, user.id, "Initial version")
    await log_activity(db, workspace_id, "file_created", user.id, file_id=file.id, details={"path": file.path})
    return FilePublic.model_validate(file)


@router.get("/{file_id}", response_model=FilePublic)
async def get_file(
    workspace_id: str,
    file_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FilePublic:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    file = await db.get(WorkspaceFile, file_id)
    if not file or file.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return FilePublic.model_validate(file)


@router.put("/{file_id}", response_model=FilePublic)
async def update_file(
    workspace_id: str,
    file_id: str,
    payload: FileUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FilePublic:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.editor)
    workspace = await db.get(Workspace, workspace_id)
    file = await db.get(WorkspaceFile, file_id)
    if not workspace or not file or file.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if workspace.is_frozen:
        raise HTTPException(status.HTTP_423_LOCKED, "Workspace editing is frozen")
    if file.is_read_only or (file.locked_by is not None and file.locked_by != user.id):
        raise HTTPException(status.HTTP_423_LOCKED, "File is locked or read-only")
    previous = file.content
    file.content = payload.content
    file.updated_at = now_utc()
    if payload.intent is not None:
        change = CodeChange(
            id=new_id("chg"),
            file_id=file.id,
            workspace_id=workspace_id,
            user_id=user.id,
            intent=payload.intent,
            line_start=payload.line_start,
            line_end=payload.line_end,
            previous_content=previous,
            content=payload.content,
            summary=payload.summary,
        )
        db.add(change)
    await db.flush()

    await manager.broadcast(workspace_id, {
        "type": "file_updated",
        "fileId": file.id,
        "content": file.content,
        "intent": payload.intent.value if hasattr(payload.intent, "value") else payload.intent,
        "user_id": user.id,
    })

    await create_version(db, file, user.id, payload.summary or (f"{payload.intent.value} update" if payload.intent else "Neutral update"))
    await log_activity(db, workspace_id, "file_updated", user.id, payload.intent, file.id)
    return FilePublic.model_validate(file)


@router.get("/{file_id}/versions", response_model=list[VersionPublic])
async def versions(
    workspace_id: str,
    file_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[VersionPublic]:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.viewer)
    result = await db.execute(select(WorkspaceFile).where(WorkspaceFile.id == file_id, WorkspaceFile.workspace_id == workspace_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    from app.models import FileVersion

    version_result = await db.execute(select(FileVersion).where(FileVersion.file_id == file_id).order_by(FileVersion.version_number.desc()))
    return [VersionPublic.model_validate(version) for version in version_result.scalars().all()]


@router.post("/{file_id}/versions/{version_id}/restore", response_model=FilePublic)
async def restore_version(
    workspace_id: str,
    file_id: str,
    version_id: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FilePublic:
    await require_workspace_role(db, workspace_id, user.id, WorkspaceRole.editor)
    from app.models import FileVersion

    file = await db.get(WorkspaceFile, file_id)
    version = await db.get(FileVersion, version_id)
    if not file or not version or file.workspace_id != workspace_id or version.file_id != file_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    file.content = version.content
    await create_version(db, file, user.id, f"Restored version {version.version_number}")
    await log_activity(db, workspace_id, "version_restored", user.id, Intent.refactoring, file.id)
    return FilePublic.model_validate(file)
