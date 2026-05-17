from fastapi import APIRouter

from app.api.v1 import activity, admin, auth, chat, files, notifications, security, workspaces

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
api_router.include_router(files.router)
api_router.include_router(chat.router)
api_router.include_router(activity.router)
api_router.include_router(notifications.router)
api_router.include_router(admin.router)
api_router.include_router(security.router)
