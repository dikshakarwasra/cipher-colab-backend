from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models import Intent, WorkspaceRole


class UserPublic(BaseModel):
    id: int
    username: str
    email: EmailStr
    display_name: str

    model_config = {"from_attributes": True}


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=160)


class LoginRequest(BaseModel):
    login: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserPublic


class RefreshRequest(BaseModel):
    refresh_token: str


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    template: str = "empty"


class WorkspacePublic(BaseModel):
    id: str
    room_id: str
    name: str
    description: str | None
    owner_id: int
    is_frozen: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class MemberPublic(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: WorkspaceRole
    muted_chat: bool = False


class MemberPublicWithStatus(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: WorkspaceRole
    muted_chat: bool = False
    is_online: bool = False
    current_intent: str | None = None


class JoinRoomRequest(BaseModel):
    room_id: str
    requested_role: WorkspaceRole = WorkspaceRole.editor


class JoinRoomResponse(BaseModel):
    status: str
    workspace_id: str
    role: WorkspaceRole | None = None


class FileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=512)
    language: str = "typescript"
    content: str = ""


class FileUpdate(BaseModel):
    content: str
    intent: Intent | None = None
    line_start: int = 1
    line_end: int = 1
    summary: str | None = None


class FilePublic(BaseModel):
    id: str
    workspace_id: str
    name: str
    path: str
    language: str
    content: str
    locked_by: int | None
    is_read_only: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChatCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    intent: Intent | None = None


class ChatPublic(BaseModel):
    id: str
    workspace_id: str
    user_id: int | None
    username: str
    content: str
    intent: Intent | None
    is_system: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ActivityPublic(BaseModel):
    id: str
    workspace_id: str
    user_id: int | None
    action: str
    intent: Intent | None
    file_id: str | None
    details: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VersionPublic(BaseModel):
    id: str
    file_id: str
    version_number: int
    content: str
    created_by: int
    message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationPublic(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    workspace_id: str | None
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RoleUpdate(BaseModel):
    user_id: int
    role: WorkspaceRole


class LockRequest(BaseModel):
    locked: bool = True


class FreezeRequest(BaseModel):
    frozen: bool = True


class MuteRequest(BaseModel):
    muted: bool = True


class InviteRequest(BaseModel):
    username: str
    role: WorkspaceRole = WorkspaceRole.editor


class IntentSummary(BaseModel):
    intent: str
    count: int


class PresencePublic(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: str
    current_intent: str | None = None
