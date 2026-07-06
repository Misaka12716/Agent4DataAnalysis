"""JWT 签发、解析与 FastAPI 鉴权依赖。"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from configs.config import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET_KEY

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


def _effective_secret_key() -> str:
    if JWT_SECRET_KEY:
        return JWT_SECRET_KEY

    # 开发环境：持久化临时密钥，避免重启后已签发 token 全部失效（InvalidSignatureError）。
    from pathlib import Path

    secret_path = Path(__file__).resolve().parents[2] / "tmp" / ".jwt_dev_secret"
    try:
        if secret_path.is_file():
            existing = secret_path.read_text(encoding="utf-8").strip()
            if existing:
                logger.warning(
                    "JWT_SECRET_KEY is not set; reusing persisted dev secret at %s. "
                    "Set JWT_SECRET_KEY in production.",
                    secret_path,
                )
                return existing
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(32)
        secret_path.write_text(generated, encoding="utf-8")
        try:
            secret_path.chmod(0o600)
        except OSError:
            pass
        logger.warning(
            "JWT_SECRET_KEY is not set; created persisted dev secret at %s. "
            "Set JWT_SECRET_KEY in production.",
            secret_path,
        )
        return generated
    except OSError:
        generated = secrets.token_urlsafe(32)
        logger.warning(
            "JWT_SECRET_KEY is not set; using an ephemeral dev-only secret "
            "(could not persist to %s). Set JWT_SECRET_KEY in production.",
            secret_path,
        )
        return generated


_DEV_SECRET = _effective_secret_key()


@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    username: str
    phone: str
    platform_role: str = "user"


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
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CurrentUser:
    # #region agent log
    def _dbg(message: str, data: dict, hypothesis_id: str) -> None:
        try:
            with open(
                "/data1/pjw/AgentPlatform/.cursor/debug-59272a.log",
                "a",
                encoding="utf-8",
            ) as f:
                f.write(
                    json.dumps(
                        {
                            "sessionId": "59272a",
                            "runId": "post-fix",
                            "hypothesisId": hypothesis_id,
                            "location": "jwt_auth.py:get_current_user",
                            "message": message,
                            "data": data,
                            "timestamp": int(time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass

    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    path = request.url.path
    # #endregion
    if credentials is None or credentials.scheme.lower() != "bearer":
        # #region agent log
        _dbg(
            "auth rejected: missing/invalid credentials",
            {
                "path": path,
                "has_credentials": credentials is not None,
                "scheme": getattr(credentials, "scheme", None),
                "has_auth_header": bool(auth_header),
                "auth_header_prefix": (auth_header or "")[:20],
                "auth_header_len": len(auth_header or ""),
                "user_agent": (request.headers.get("user-agent") or "")[:80],
            },
            "H1,H2",
        )
        # #endregion
        raise HTTPException(
            status_code=401,
            detail={"code": 6, "msg": "unauthorized"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError as e:
        # #region agent log
        _dbg(
            "auth rejected: invalid token",
            {
                "path": path,
                "token_len": len(credentials.credentials or ""),
                "error_type": type(e).__name__,
                "error": str(e)[:120],
            },
            "H3",
        )
        # #endregion
        raise HTTPException(
            status_code=401,
            detail={"code": 6, "msg": "unauthorized"},
        ) from None

    user_id = int(payload.get("user_id") or payload.get("sub") or 0)
    if user_id <= 0:
        # #region agent log
        _dbg(
            "auth rejected: invalid user_id in payload",
            {"path": path, "user_id": user_id},
            "H3",
        )
        # #endregion
        raise HTTPException(
            status_code=401,
            detail={"code": 6, "msg": "unauthorized"},
        )

    from backend.permission_service import is_user_blocked
    from db.rbac_store import RbacStore

    user_row, err = RbacStore.get_user(user_id)
    if err:
        # #region agent log
        _dbg(
            "auth failed: get_user error",
            {"path": path, "user_id": user_id, "error": str(err)[:120]},
            "H4",
        )
        # #endregion
        raise HTTPException(status_code=500, detail=f"查询用户失败: {err}")
    if not user_row:
        # #region agent log
        _dbg(
            "auth rejected: user not found",
            {"path": path, "user_id": user_id},
            "H4",
        )
        # #endregion
        raise HTTPException(
            status_code=401,
            detail={"code": 6, "msg": "unauthorized"},
        )
    if is_user_blocked(user_row):
        raise HTTPException(
            status_code=403,
            detail={"code": 3, "msg": "user is blocked"},
        )

    platform_role = str(user_row.get("platform_role") or "user").strip().lower()
    # #region agent log
    _dbg(
        "auth success",
        {"path": path, "user_id": user_id, "platform_role": platform_role},
        "H5",
    )
    # #endregion
    return CurrentUser(
        user_id=user_id,
        username=str(user_row.get("username") or payload.get("username") or ""),
        phone=str(user_row.get("phone") or payload.get("phone") or ""),
        platform_role=platform_role,
    )
