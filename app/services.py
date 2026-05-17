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


# Seed file templates per workspace template type
TEMPLATE_SEEDS: dict[str, list[dict]] = {
    "python": [
        {"name": "app.py", "path": "app.py", "language": "python",
         "content": 'from flask import Flask, request, jsonify\nfrom utils.db import get_db\n\napp = Flask(__name__)\n\n\n@app.route(\'/api/hello\', methods=[\'GET\'])\ndef hello():\n    return jsonify({"message": "Hello from Cipher Collab!"})\n\n\n@app.route(\'/api/user\', methods=[\'POST\'])\ndef create_user():\n    data = request.json\n    db = get_db()\n    user = db.users.insert_one(data)\n    return jsonify({"id": str(user.inserted_id), "status": "created"})\n\n\n@app.route(\'/api/users\', methods=[\'GET\'])\ndef get_users():\n    db = get_db()\n    users = list(db.users.find({}, {"_id": 0}))\n    return jsonify(users)\n'},
        {"name": "config.py", "path": "config.py", "language": "python",
         "content": 'import os\n\nDATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")\nSECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")\nDEBUG = os.getenv("DEBUG", "true").lower() == "true"\n'},
        {"name": "requirements.txt", "path": "requirements.txt", "language": "plaintext",
         "content": 'flask>=3.0.0\npymongo>=4.6.0\npython-dotenv>=1.0.0\ngunicorn>=21.2.0\n'},
        {"name": "README.md", "path": "README.md", "language": "markdown",
         "content": '# Cipher Collab Python Backend\n\nA collaborative Python backend project.\n\n## Setup\n\n```bash\npip install -r requirements.txt\npython app.py\n```\n\n## API Endpoints\n\n- `GET /api/hello` – Health check\n- `POST /api/user` – Create user\n- `GET /api/users` – List users\n'},
        {"name": "auth.js", "path": "backend/routes/auth.js", "language": "javascript",
         "content": 'const express = require(\'express\');\nconst router = express.Router();\n\nrouter.post(\'/login\', async (req, res) => {\n  const { username, password } = req.body;\n  // TODO: validate credentials\n  res.json({ token: \'jwt-token-here\' });\n});\n\nmodule.exports = router;\n'},
        {"name": "user.js", "path": "backend/routes/user.js", "language": "javascript",
         "content": 'const express = require(\'express\');\nconst router = express.Router();\n\nrouter.get(\'/\', async (req, res) => {\n  const users = await User.find();\n  res.json(users);\n});\n\nrouter.post(\'/\', async (req, res) => {\n  const user = new User(req.body);\n  await user.save();\n  res.status(201).json(user);\n});\n\nmodule.exports = router;\n'},
        {"name": "project.js", "path": "backend/routes/project.js", "language": "javascript",
         "content": 'const express = require(\'express\');\nconst router = express.Router();\n\nrouter.get(\'/\', async (req, res) => {\n  const projects = await Project.find({ owner: req.user.id });\n  res.json(projects);\n});\n\nmodule.exports = router;\n'},
        {"name": "userModel.js", "path": "backend/models/userModel.js", "language": "javascript",
         "content": 'const mongoose = require(\'mongoose\');\n\nconst userSchema = new mongoose.Schema({\n  username: { type: String, required: true, unique: true },\n  email: { type: String, required: true, unique: true },\n  password: { type: String, required: true },\n  createdAt: { type: Date, default: Date.now },\n});\n\nmodule.exports = mongoose.model(\'User\', userSchema);\n'},
        {"name": "projectModel.js", "path": "backend/models/projectModel.js", "language": "javascript",
         "content": 'const mongoose = require(\'mongoose\');\n\nconst projectSchema = new mongoose.Schema({\n  name: { type: String, required: true },\n  owner: { type: mongoose.Schema.Types.ObjectId, ref: \'User\' },\n  files: [{ type: String }],\n  createdAt: { type: Date, default: Date.now },\n});\n\nmodule.exports = mongoose.model(\'Project\', projectSchema);\n'},
        {"name": "helper.js", "path": "backend/utils/helper.js", "language": "javascript",
         "content": 'exports.formatError = (message, code = 400) => ({ error: message, code });\nexports.paginate = (query, page = 1, limit = 20) => query.skip((page - 1) * limit).limit(limit);\n'},
        {"name": "db.js", "path": "backend/utils/db.js", "language": "javascript",
         "content": 'const mongoose = require(\'mongoose\');\n\nlet _db;\n\nexports.connect = async () => {\n  _db = await mongoose.connect(process.env.MONGO_URI);\n  console.log(\'DB connected\');\n};\n\nexports.get_db = () => _db;\n'},
        {"name": "index.ts", "path": "frontend/index.ts", "language": "typescript",
         "content": 'import { createApp } from \'./app\';\n\nconst app = createApp();\n\napp.listen(3000, () => {\n  console.log(\'Frontend server running on port 3000\');\n});\n'},
        {"name": ".gitignore", "path": ".gitignore", "language": "plaintext",
         "content": '__pycache__/\n*.pyc\n.env\nnode_modules/\ndist/\n.DS_Store\n*.log\n'},
        {"name": "Dockerfile", "path": "Dockerfile", "language": "dockerfile",
         "content": 'FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install -r requirements.txt\nCOPY . .\nEXPOSE 5000\nCMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]\n'},
    ],
    "node": [
        {"name": "index.ts", "path": "src/index.ts", "language": "typescript",
         "content": 'import express from \'express\';\n\nconst app = express();\napp.use(express.json());\n\napp.get(\'/\', (_req, res) => res.json({ message: \'Hello from Cipher Collab\' }));\n\napp.listen(3000, () => console.log(\'Server running on port 3000\'));\n'},
        {"name": "README.md", "path": "README.md", "language": "markdown",
         "content": '# Cipher Collab Node Backend\n\n```bash\nnpm install\nnpm run dev\n```\n'},
    ],
    "empty": [
        {"name": "index.ts", "path": "src/index.ts", "language": "typescript",
         "content": '// Cipher Collab Workspace\n// Start collaborating!\n\nconsole.log("Hello from Cipher Collab");\n'},
        {"name": "README.md", "path": "README.md", "language": "markdown",
         "content": '# New Workspace\n\nWelcome to your new Cipher Collab workspace.\n'},
    ],
}


async def create_workspace_with_seed(
    db: AsyncSession,
    owner: User,
    name: str,
    description: str | None,
    template: str,
) -> Workspace:
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

    seeds = TEMPLATE_SEEDS.get(template, TEMPLATE_SEEDS["empty"])
    for seed_data in seeds:
        file = WorkspaceFile(
            id=new_id("fil"),
            workspace_id=workspace.id,
            name=seed_data["name"],
            path=seed_data["path"],
            language=seed_data["language"],
            content=seed_data["content"],
            created_by=owner.id,
        )
        db.add(file)
        await db.flush()
        db.add(
            FileVersion(
                id=new_id("ver"),
                file_id=file.id,
                workspace_id=workspace.id,
                version_number=1,
                content=file.content,
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
