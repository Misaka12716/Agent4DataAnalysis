"""JWT 鉴权与 session 归属校验测试。"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

# 避免测试收集阶段连接 MySQL
if "utils.mysql_utils" not in sys.modules:
    _mysql_mod = MagicMock()
    _mysql_mod.mysql_handler = MagicMock()
    sys.modules["utils.mysql_utils"] = _mysql_mod

from backend.jwt_auth import create_access_token, decode_access_token
from backend.session_auth import assert_session_owner
from configs.config import JWT_ALGORITHM


def test_create_and_decode_access_token():
    token, expires_in = create_access_token(42, "alice", "13800138000")
    assert expires_in > 0
    payload = decode_access_token(token)
    assert payload["user_id"] == 42
    assert payload["username"] == "alice"
    assert payload["phone"] == "13800138000"
    assert payload["sub"] == "42"


def test_decode_expired_token_raises():
    from backend import jwt_auth

    secret = jwt_auth._DEV_SECRET
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "1",
        "user_id": 1,
        "username": "u",
        "phone": "13800138000",
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
    }
    token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token)


def test_assert_session_owner_success():
    session_row = {"session_id": "sid-1", "user_id": 10, "workspace_abs_path": "/tmp/ws"}
    with patch("db.session_store.SessionStore.get_session_user", return_value=(session_row, None)):
        result = assert_session_owner("sid-1", 10)
    assert result["session_id"] == "sid-1"


def test_assert_session_owner_forbidden():
    session_row = {"session_id": "sid-1", "user_id": 10, "project_id": 1, "workspace_abs_path": "/tmp/ws"}
    with patch("db.session_store.SessionStore.get_session_user", return_value=(session_row, None)):
        with patch(
            "backend.project_auth.assert_project_access",
            side_effect=HTTPException(
                status_code=403,
                detail={"code": 7, "msg": "forbidden: session access denied"},
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                assert_session_owner("sid-1", 99)
    assert exc.value.status_code == 403


def test_assert_session_owner_not_found():
    with patch("db.session_store.SessionStore.get_session_user", return_value=(None, None)):
        with pytest.raises(HTTPException) as exc:
            assert_session_owner("missing", 1)
    assert exc.value.status_code == 404
