import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import create_all
from app.main import app


@pytest.mark.asyncio
async def test_signup_create_workspace_and_file_flow():
    await create_all()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        signup = await client.post(
            "/api/v1/auth/signup",
            json={
                "username": "demo_user",
                "email": "demo@example.com",
                "password": "StrongPass123!",
                "display_name": "Demo User",
            },
        )
        assert signup.status_code in {201, 409}

        login = await client.post(
            "/api/v1/auth/login",
            json={"login": "demo_user", "password": "StrongPass123!"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        workspace = await client.post(
            "/api/v1/workspaces",
            json={"name": "Test Workspace", "template": "python"},
            headers=headers,
        )
        assert workspace.status_code == 201
        workspace_id = workspace.json()["id"]

        files = await client.get(f"/api/v1/workspaces/{workspace_id}/files", headers=headers)
        assert files.status_code == 200
        assert len(files.json()) >= 1

        file_id = files.json()[0]["id"]
        duplicate = await client.post(
            f"/api/v1/workspaces/{workspace_id}/files",
            json={
                "name": files.json()[0]["name"],
                "path": files.json()[0]["path"],
                "language": files.json()[0]["language"],
                "content": "",
            },
            headers=headers,
        )
        assert duplicate.status_code == 409

        update = await client.put(
            f"/api/v1/workspaces/{workspace_id}/files/{file_id}",
            json={
                "content": "print('updated')\n",
                "intent": "debugging",
                "line_start": 1,
                "line_end": 1,
            },
            headers=headers,
        )
        assert update.status_code == 200
        assert update.json()["content"] == "print('updated')\n"
