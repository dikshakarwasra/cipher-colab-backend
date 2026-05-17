import base64
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _create_token(user_id: int, secret: str, token_type: str, expires_delta: timedelta) -> str:
    expires_at = datetime.now(UTC) + expires_delta
    payload = {"sub": str(user_id), "type": token_type, "exp": expires_at, "jti": base64.urlsafe_b64encode(os.urandom(18)).decode("ascii")}
    return jwt.encode(payload, secret, algorithm="HS256")


def create_access_token(user_id: int) -> str:
    return _create_token(
        user_id,
        settings.jwt_access_secret,
        "access",
        timedelta(minutes=settings.access_token_minutes),
    )


def create_refresh_token(user_id: int) -> str:
    return _create_token(
        user_id,
        settings.jwt_refresh_secret,
        "refresh",
        timedelta(days=settings.refresh_token_days),
    )


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_access_secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise credentials_error
        user_id = int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        raise credentials_error from None

    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def aes_encrypt(plaintext: str) -> str:
    key = base64.urlsafe_b64decode(settings.aes_gcm_key)
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def aes_decrypt(ciphertext: str) -> str:
    payload = base64.urlsafe_b64decode(ciphertext)
    nonce, encrypted = payload[:12], payload[12:]
    key = base64.urlsafe_b64decode(settings.aes_gcm_key)
    return AESGCM(key).decrypt(nonce, encrypted, None).decode("utf-8")
