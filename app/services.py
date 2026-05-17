import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import aes_encrypt, create_access_token, create_refresh_token, hash_password, verify_password
from app.models import (
    ActivityLog,
    ChatMessage,
    CodeChange,
    FileVersion,
    Intent,
    JoinRequest,
    Notification,
    RefreshSession,
    SecurityLog,
    User,
    Workspace,
    WorkspaceFile,
    WorkspaceMember,
    WorkspaceRole,
)
from app.schemas import SignupRequest


ROLE_POWER = {WorkspaceRole.viewer: 1, WorkspaceRole.editor: 2, WorkspaceRole.admin: 3}


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:18]}"


def room_id() -> str:
    token = secrets.token_hex(4).upper()
    return f"DEV-{token[:4]}-{token[4:]}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def log_security(
    db: AsyncSession,
    event: str,
    request: Request | None = None,
    user_id: int | None = None,
    details: dict | None = None,
) -> None:
    db.add(
        SecurityLog(
            id=new_id("sec"),
            event=event,
            user_id=user_id,
            ip_address=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
            details=json.dumps(details or {}),
        )
    )


async def create_user(db: AsyncSession, payload: SignupRequest) -> User:
    existing = await db.execute(
        select(User).where(or_(User.email == payload.email, User.username == payload.username))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already exists")
    user = User(
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def authenticate(db: AsyncSession, login: str, password: str) -> User | None:
    result = await db.execute(select(User).where(or_(User.email == login, User.username == login)))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


async def issue_tokens(db: AsyncSession, user: User, request: Request | None = None) -> tuple[str, str]:
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    db.add(
        RefreshSession(
            user_id=user.id,
            token_hash=token_hash(refresh),
            user_agent=request.headers.get("user-agent") if request else None,
            ip_address=request.client.host if request and request.client else None,
            expires_at=now_utc() + timedelta(days=settings.refresh_token_days),
        )
    )
    return access, refresh


async def get_workspace_role(db: AsyncSession, workspace_id: str, user_id: int) -> WorkspaceRole | None:
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    return WorkspaceRole(member.role) if member else None


async def require_workspace_role(
    db: AsyncSession,
    workspace_id: str,
    user_id: int,
    role: WorkspaceRole,
) -> WorkspaceRole:
    actual = await get_workspace_role(db, workspace_id, user_id)
    if actual is None or ROLE_POWER[actual] < ROLE_POWER[role]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{role.value} access required")
    return actual


async def log_activity(
    db: AsyncSession,
    workspace_id: str,
    action: str,
    user_id: int | None = None,
    intent: Intent | None = None,
    file_id: str | None = None,
    details: dict | None = None,
) -> ActivityLog:
    entry = ActivityLog(
        id=new_id("act"),
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        intent=intent,
        file_id=file_id,
        details=json.dumps(details or {}),
    )
    db.add(entry)
    return entry


async def notify_user(
    db: AsyncSession,
    user_id: int,
    title: str,
    body: str,
    kind: str,
    workspace_id: str | None = None,
) -> Notification:
    notification = Notification(
        id=new_id("not"),
        user_id=user_id,
        title=title,
        body=body,
        kind=kind,
        workspace_id=workspace_id,
    )
    db.add(notification)
    return notification


def workspace_query_for_user(user_id: int) -> Select[tuple[Workspace]]:
    return (
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.updated_at.desc())
    )


async def create_workspace_with_seed(db: AsyncSession, owner: User, name: str, description: str | None, template: str) -> Workspace:
    workspace = Workspace(
        id=new_id("wrk"),
        room_id=room_id(),
        name=name,
        description=description,
        owner_id=owner.id,
        encrypted_metadata=aes_encrypt(json.dumps({"template": template, "backup_interval_seconds": 30})),
    )
    db.add(workspace)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role=WorkspaceRole.admin))
    seed = WorkspaceFile(
        id=new_id("fil"),
        workspace_id=workspace.id,
        name="app.py" if template == "python" else "index.ts",
        path="app.py" if template == "python" else "src/index.ts",
        language="python" if template == "python" else "typescript",
        content='print("Hello from Cipher Collab")\n' if template == "python" else 'console.log("Hello from Cipher Collab");\n',
        created_by=owner.id,
    )
    db.add(seed)
    await db.flush()
    db.add(
        FileVersion(
            id=new_id("ver"),
            file_id=seed.id,
            workspace_id=workspace.id,
            version_number=1,
            content=seed.content,
            created_by=owner.id,
            message="Initial version",
        )
    )
    await log_activity(db, workspace.id, "workspace_created", owner.id, details={"name": name})
    return workspace


async def next_file_version(db: AsyncSession, file_id: str) -> int:
    result = await db.execute(select(func.max(FileVersion.version_number)).where(FileVersion.file_id == file_id))
    current = result.scalar_one_or_none() or 0
    return int(current) + 1


async def create_version(db: AsyncSession, file: WorkspaceFile, user_id: int, message: str | None = None) -> FileVersion:
    version = FileVersion(
        id=new_id("ver"),
        file_id=file.id,
        workspace_id=file.workspace_id,
        version_number=await next_file_version(db, file.id),
        content=file.content,
        created_by=user_id,
        message=message,
    )
    db.add(version)
    return version


async def set_workspace_frozen(db: AsyncSession, workspace_id: str, frozen: bool) -> None:
    await db.execute(update(Workspace).where(Workspace.id == workspace_id).values(is_frozen=frozen))
