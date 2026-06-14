"""JWT 签发、解析与 FastAPI 鉴权依赖。"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from configs.config import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET_KEY

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


def _effective_secret_key() -> str:
    if JWT_SECRET_KEY:
        return JWT_SECRET_KEY
    generated = secrets.token_urlsafe(32)
    logger.warning(
        "JWT_SECRET_KEY is not set; using an ephemeral dev-only secret. "
        "Set JWT_SECRET_KEY in production."
    )
    return generated


_DEV_SECRET = _effective_secret_key()


@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    username: str
    phone: str


def create_access_token(user_id: int, username: str, phone: str) -> tuple[str, int]:
    """签发 access token，返回 (token, expires_in_seconds)。"""
    expires_in = JWT_EXPIRE_HOURS * 3600
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "user_id": user_id,
        "username": username,
        "phone": phone,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, _DEV_SECRET, algorithm=JWT_ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> dict[str, Any]:
    """解析并校验 token，失败时抛出 jwt.InvalidTokenError。"""
    return jwt.decode(token, _DEV_SECRET, algorithms=[JWT_ALGORITHM])


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": 6, "msg": "unauthorized"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail={"code": 6, "msg": "unauthorized"},
        ) from None

    user_id = int(payload.get("user_id") or payload.get("sub") or 0)
    if user_id <= 0:
        raise HTTPException(
            status_code=401,
            detail={"code": 6, "msg": "unauthorized"},
        )
    return CurrentUser(
        user_id=user_id,
        username=str(payload.get("username") or ""),
        phone=str(payload.get("phone") or ""),
    )
