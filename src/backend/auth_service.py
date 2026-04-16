import random
import re
import time
import hashlib
import os
from typing import Any

from fastapi.responses import JSONResponse
import requests

SMS_CODE_EXPIRE_SECONDS = 120
_PHONE_CODE_CACHE: dict[str, dict[str, Any]] = {}
_PHONE_PATTERN = re.compile(r"^1\d{10}$")
_SMS_APP_ID = os.getenv("SMS_APP_ID", "EUCP-EMY-SMS1-05RA6")
_SMS_SECRET_KEY = os.getenv("SMS_SECRET_KEY", "F1D5A562AED35AE3")
_SMS_SIGN_NAME = os.getenv("SMS_SIGN_NAME", "【六元空间】")
_SMS_URL = os.getenv("SMS_URL", "http://bjksmtn.b2m.cn:80/simpleinter/sendSMS")


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
    content = f"尊敬的用户，您正在进行【数据分析&科学发现智能体】账号验证登录/注册，验证码：{verification_code}，有效期2分钟。请勿向他人泄露验证码，谨防诈骗，如非本人操作请忽略本短信。"
    timestamp = time.strftime("%Y%m%d%H%M%S", time.localtime())
    sign = hashlib.md5(f"{_SMS_APP_ID}{_SMS_SECRET_KEY}{timestamp}".encode("utf-8")).hexdigest()
    params = {
        "appId": _SMS_APP_ID,
        "timestamp": timestamp,
        "sign": sign,
        "mobiles": phone,
        "content": f"{_SMS_SIGN_NAME}{content}",
    }

    try:
        response = requests.post(_SMS_URL, data=params, timeout=8)
        response.raise_for_status()
        response_json = response.json()
    except requests.RequestException as exc:
        return JSONResponse(
            content={"code": 4, "msg": f"Error sending SMS: {exc}"},
            status_code=502,
        )
    except ValueError:
        return JSONResponse(
            content={"code": 4, "msg": "Error sending SMS: invalid gateway response"},
            status_code=502,
        )

    if str(response_json.get("code", "")).upper() != "SUCCESS":
        msg = response_json.get("msg", "unknown error")
        return JSONResponse(
            content={"code": 4, "msg": f"Failed to send SMS: {msg}"},
            status_code=502,
        )

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
                "expires_in": SMS_CODE_EXPIRE_SECONDS,
            },
        },
        status_code=200,
    )


def build_login_with_sms_response(phone: str, code: str) -> JSONResponse:
    if _invalid_phone(phone):
        return JSONResponse(
            content={"code": 1, "msg": "missing or invalid parameter: phone"},
            status_code=400,
        )
    if not code:
        return JSONResponse(
            content={"code": 1, "msg": "missing parameter: code"},
            status_code=400,
        )

    cached_code_info = _PHONE_CODE_CACHE.get(phone)
    now_ts = int(time.time())
    if not cached_code_info:
        return JSONResponse(
            content={"code": 5, "msg": "verification code is incorrect or expired"},
            status_code=400,
        )
    if now_ts > int(cached_code_info.get("expires_at", 0)):
        _PHONE_CODE_CACHE.pop(phone, None)
        return JSONResponse(
            content={"code": 5, "msg": "verification code is incorrect or expired"},
            status_code=400,
        )
    if str(cached_code_info.get("code")) != str(code).strip():
        return JSONResponse(
            content={"code": 5, "msg": "verification code is incorrect or expired"},
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

    # 验证码一次性消费，防止重复使用。
    _PHONE_CODE_CACHE.pop(phone, None)

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
