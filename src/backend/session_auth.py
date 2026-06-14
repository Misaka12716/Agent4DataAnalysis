"""会话归属校验。"""

from fastapi import HTTPException

from db.models import SessionUserRow
from db.session_store import SessionStore


def assert_session_owner(session_id: str, current_user_id: int) -> SessionUserRow:
    """校验 session 存在且属于当前用户。"""
    sid = session_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    session_user, err = SessionStore.get_session_user(sid)
    if err:
        raise HTTPException(status_code=500, detail=f"查询会话失败: {err}")
    if not session_user:
        raise HTTPException(status_code=404, detail="session_id 不存在，请先创建会话")
    owner_id = int(session_user.get("user_id") or 0)
    if owner_id != current_user_id:
        raise HTTPException(
            status_code=403,
            detail={"code": 7, "msg": "forbidden: session access denied"},
        )
    return session_user
