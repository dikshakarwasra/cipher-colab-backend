from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import CurrentUser
from app.models import RefreshSession, User
from app.schemas import LoginRequest, RefreshRequest, SignupRequest, TokenResponse, UserPublic
from app.services import authenticate, create_user, issue_tokens, log_security, now_utc, token_hash

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    user = await create_user(db, payload)
    await log_security(db, "signup_success", request, user.id)
    access, refresh = await issue_tokens(db, user, request)
    return TokenResponse(access_token=access, refresh_token=refresh, user=UserPublic.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    user = await authenticate(db, payload.login, payload.password)
    if not user:
        await log_security(db, "login_failed", request, details={"login": payload.login})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username/email or password")
    await log_security(db, "login_success", request, user.id)
    access, refresh = await issue_tokens(db, user, request)
    return TokenResponse(access_token=access, refresh_token=refresh, user=UserPublic.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    try:
        decoded = jwt.decode(payload.refresh_token, settings.jwt_refresh_secret, algorithms=["HS256"])
        if decoded.get("type") != "refresh":
            raise ValueError("wrong token type")
        user_id = int(decoded["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        await log_security(db, "refresh_failed", request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from None

    result = await db.execute(
        select(RefreshSession).where(
            RefreshSession.user_id == user_id,
            RefreshSession.token_hash == token_hash(payload.refresh_token),
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > now_utc(),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        await log_security(db, "refresh_reuse_or_missing", request, user_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh session is not active")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    await log_security(db, "refresh_success", request, user.id)
    access, refresh_token = await issue_tokens(db, user, request)
    session.revoked_at = now_utc()
    return TokenResponse(access_token=access, refresh_token=refresh_token, user=UserPublic.model_validate(user))


@router.get("/me", response_model=UserPublic)
async def me(user: CurrentUser) -> UserPublic:
    return UserPublic.model_validate(user)


@router.post("/logout")
async def logout(
    payload: RefreshRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool]:
    await db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.token_hash == token_hash(payload.refresh_token))
        .values(revoked_at=now_utc())
    )
    return {"success": True}


@router.post("/logout-all")
async def logout_all(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, bool]:
    await db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    return {"success": True}
