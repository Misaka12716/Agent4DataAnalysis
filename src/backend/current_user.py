"""单用户模式：固定默认用户依赖。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

DEFAULT_USER_ID = 1


@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    username: str
    phone: str
    platform_role: str = "user"


def get_default_user() -> CurrentUser:
    from db.rbac_store import RbacStore

    user_row, err = RbacStore.get_user(DEFAULT_USER_ID)
    if err:
        raise HTTPException(status_code=500, detail=f"查询用户失败: {err}")
    if not user_row:
        raise HTTPException(
            status_code=503,
            detail={
                "code": 10,
                "msg": "默认用户未初始化，请先运行 bash scripts/init-platform.sh",
            },
        )
    return CurrentUser(
        user_id=DEFAULT_USER_ID,
        username=str(user_row.get("username") or "default"),
        phone=str(user_row.get("phone") or ""),
        platform_role=str(user_row.get("platform_role") or "user").strip().lower(),
    )
