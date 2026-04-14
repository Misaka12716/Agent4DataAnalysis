import random
import re
import time
import hashlib
from typing import Any

from fastapi.responses import JSONResponse

SMS_CODE_EXPIRE_SECONDS = 120
_PHONE_CODE_CACHE: dict[str, dict[str, Any]] = {}
_PHONE_PATTERN = re.compile(r"^1\d{10}$")


def _invalid_phone(phone: str) -> bool:
    return (not phone) or (not _PHONE_PATTERN.match(phone))


def _is_user_blocked(user_row: dict[str, Any]) -> bool:
    # 兼容不同表结构：只要显式标记为 blocked/disabled 都认为不可登录。
    blocked = user_row.get("is_blocked")
    if blocked is not None and bool(blocked):
        return True

    status = str(user_row.get("status", "")).strip().lower()
    return status in {"blocked", "disabled", "inactive"}


def build_send_sms_code_response(phone: str) -> JSONResponse:
    if _invalid_phone(phone):
        return JSONResponse(
            content={"code": 1, "msg": "missing or invalid parameter: phone"},
            status_code=400,
        )

    verification_code = str(random.randint(100000, 999999))
    _PHONE_CODE_CACHE[phone] = {
        "code": verification_code,
        "expires_at": int(time.time()) + SMS_CODE_EXPIRE_SECONDS,
    }
    return JSONResponse(
        content={
            "code": 0,
            "msg": "SMS code sent successfully",
            "data": {
                "phone": phone,
                "verification_code": verification_code,
                "expires_in": SMS_CODE_EXPIRE_SECONDS,
            },
        },
        status_code=200,
    )


def build_login_with_sms_response(phone: str) -> JSONResponse:
    if _invalid_phone(phone):
        return JSONResponse(
            content={"code": 1, "msg": "missing or invalid parameter: phone"},
            status_code=400,
        )

    from utils.mysql_utils import mysql_handler

    users, err = mysql_handler.query(
        "SELECT * FROM users WHERE phone = %s LIMIT 1",
        (phone,),
    )
    if err:
        return JSONResponse(
            content={"code": 4, "msg": "database error", "detail": err},
            status_code=500,
        )

    if not users:
        # 不存在则自动注册，再按登录成功返回。
        username = f"user_{phone[-4:]}_{int(time.time())}"
        password_hash = hashlib.sha256(f"sms-login:{phone}".encode("utf-8")).hexdigest()
        affected, err = mysql_handler.execute(
            "INSERT INTO users (username, phone, password_hash) VALUES (%s, %s, %s)",
            (username, phone, password_hash),
        )
        if err or affected <= 0:
            return JSONResponse(
                content={"code": 4, "msg": "register failed", "detail": err},
                status_code=500,
            )
        users, err = mysql_handler.query(
            "SELECT * FROM users WHERE phone = %s LIMIT 1",
            (phone,),
        )
        if err or not users:
            return JSONResponse(
                content={"code": 4, "msg": "register success but query failed", "detail": err},
                status_code=500,
            )

    user = users[0]
    if _is_user_blocked(user):
        return JSONResponse(
            content={"code": 3, "msg": "user is blocked"},
            status_code=403,
        )

    return JSONResponse(
        content={
            "code": 0,
            "msg": "login success",
            "data": {
                "user_id": user.get("id"),
                "username": user.get("username"),
                "phone": user.get("phone"),
            },
        },
        status_code=200,
    )
