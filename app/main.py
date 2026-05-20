import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from sqlalchemy import select

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal, create_all
from app.models import User, WorkspaceRole
from app.services import get_workspace_role
from app.websocket_manager import manager


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_all()
    yield


app = FastAPI(
    title="Cipher Collab API",
    version="2.1.0",
    description="Secure real-time collaboration backend for Cipher Collab.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin) for origin in settings.cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "api": "fastapi",
        "version": "2.1.0",
        "websocket": "/ws/workspaces/{workspace_id}",
    }


async def websocket_user(token: str) -> User | None:
    try:
        payload = jwt.decode(token, settings.jwt_access_secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            return None
        user_id = int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        return None
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


@app.websocket("/ws/workspaces/{workspace_id}")
async def workspace_socket(websocket: WebSocket, workspace_id: str, token: str):
    user = await websocket_user(token)
    if user is None:
        await websocket.close(code=4401)
        return
    async with AsyncSessionLocal() as db:
        role = await get_workspace_role(db, workspace_id, user.id)
    if role is None:
        await websocket.close(code=4403)
        return

    user_payload = {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "role": role.value if isinstance(role, WorkspaceRole) else str(role),
    }
    await manager.connect(workspace_id, websocket, user_payload)
    try:
        while True:
            raw = await websocket.receive_text()
            payload = json.loads(raw)

            if not isinstance(payload, dict):
                continue

            payload.setdefault("user", user_payload)
            payload.setdefault("workspaceId", workspace_id)

            msg_type = payload.get("type")

            if msg_type == "intent_change" and "intent" in payload:
                manager.set_user_intent(websocket, payload["intent"])
                await manager.broadcast(workspace_id, payload, exclude=websocket)

            elif msg_type == "intent_range":
                # Broadcast live editing range to other users
                await manager.broadcast(workspace_id, payload, exclude=websocket)

            elif msg_type == "user_status_update":
                # Update and broadcast member status changes
                if "intent" in payload:
                    manager.set_user_intent(websocket, payload["intent"])
                await manager.broadcast(workspace_id, payload, exclude=websocket)

            elif msg_type in {
                "yjs_update",
                "cursor_update",
                "typing",
                "chat_message",
                "file_saved",
                "file_locked",
                "file_unlocked",
                "workspace_frozen",
                "workspace_unfrozen",
                "notification",
            }:
                await manager.broadcast(workspace_id, payload, exclude=websocket)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json({"type": "error", "message": "Unsupported websocket event"})

    except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
        await manager.disconnect(workspace_id, websocket)
