import json
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self.users: dict[WebSocket, dict] = {}
        self.user_intents: dict[WebSocket, str] = {}

    async def connect(self, workspace_id: str, websocket: WebSocket, user: dict) -> None:
        await websocket.accept()
        self.rooms[workspace_id].add(websocket)
        self.users[websocket] = {**user, "workspace_id": workspace_id}
        await self.broadcast(workspace_id, {"type": "user_joined", "user": user}, exclude=websocket)
        online = self.get_online_user_ids(workspace_id)
        await websocket.send_json({"type": "presence_sync", "online_user_ids": online})

    async def disconnect(self, workspace_id: str, websocket: WebSocket) -> None:
        user = self.users.pop(websocket, None)
        self.user_intents.pop(websocket, None)
        self.rooms[workspace_id].discard(websocket)
        if user:
            await self.broadcast(workspace_id, {"type": "user_left", "user": user})

    async def broadcast(self, workspace_id: str, payload: dict, exclude: WebSocket | None = None) -> None:
        # Use a more robust serializer for Enums and other types
        def serializer(obj):
            if hasattr(obj, "value"):
                return obj.value
            return str(obj)

        message = json.dumps(payload, default=serializer)
        stale: list[WebSocket] = []
        for socket in list(self.rooms.get(workspace_id, set())):
            if socket is exclude:
                continue
            try:
                await socket.send_text(message)
            except (RuntimeError, Exception):
                stale.append(socket)
        for socket in stale:
            self.rooms[workspace_id].discard(socket)
            self.users.pop(socket, None)
            self.user_intents.pop(socket, None)

    def get_online_user_ids(self, workspace_id: str) -> list[int]:
        ids: list[int] = []
        for ws in self.rooms.get(workspace_id, set()):
            user = self.users.get(ws)
            if user and "id" in user:
                ids.append(int(user["id"]))
        return ids

    def get_online_users(self, workspace_id: str) -> list[dict]:
        result: list[dict] = []
        for ws in self.rooms.get(workspace_id, set()):
            user = self.users.get(ws)
            if user:
                entry = {**user}
                entry["current_intent"] = self.user_intents.get(ws)
                result.append(entry)
        return result

    def set_user_intent(self, websocket: WebSocket, intent: str) -> None:
        self.user_intents[websocket] = intent


manager = ConnectionManager()
