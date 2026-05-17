# Cipher Colab Backend

FastAPI backend for Cipher Colab with async SQLAlchemy, JWT authentication, role-based workspace access, chat, files, activity, notifications, and WebSocket collaboration events.

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

On Windows PowerShell, activate with:

```powershell
.\\.venv\\Scripts\\Activate.ps1
```

## Environment

- `APP_ENV`: `development`, `test`, or `production`.
- `DATABASE_URL`: SQLAlchemy async connection string. SQLite works locally; MySQL uses `mysql+aiomysql://user:password@host:3306/database`.
- `JWT_ACCESS_SECRET`: secret for access tokens.
- `JWT_REFRESH_SECRET`: secret for refresh tokens.
- `AES_GCM_KEY`: base64 URL-safe 32-byte key for AES-256-GCM helpers.
- `CORS_ORIGINS`: JSON list of allowed frontend origins.
- `FRONTEND_URL`: public frontend URL.

## Scripts

- `alembic upgrade head`: apply database migrations.
- `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`: run the API.
- `pytest`: run backend tests.

# FIXED: added clean backend repo documentation and env contract

