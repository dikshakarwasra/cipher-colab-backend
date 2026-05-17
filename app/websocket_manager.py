import json
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self.users: dict[WebSocket, dict] = {}

    async def connect(self, workspace_id: str, websocket: WebSocket, user: dict) -> None:
        await websocket.accept()
        self.rooms[workspace_id].add(websocket)
        self.users[websocket] = user
        await self.broadcast(workspace_id, {"type": "user_joined", "user": user})

    async def disconnect(self, workspace_id: str, websocket: WebSocket) -> None:
        user = self.users.pop(websocket, None)
        self.rooms[workspace_id].discard(websocket)
        if user:
            await self.broadcast(workspace_id, {"type": "user_left", "user": user})

    async def broadcast(self, workspace_id: str, payload: dict, exclude: WebSocket | None = None) -> None:
        message = json.dumps(payload, default=str)
        stale: list[WebSocket] = []
        for socket in self.rooms.get(workspace_id, set()):
            if socket is exclude:
                continue
            try:
                await socket.send_text(message)
            except RuntimeError:
                stale.append(socket)
        for socket in stale:
            self.rooms[workspace_id].discard(socket)


manager = ConnectionManager()
