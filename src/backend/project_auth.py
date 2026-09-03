"""项目与会话存在性校验（单用户模式）。"""

from __future__ import annotations

from fastapi import HTTPException

from db.models import SessionUserRow
from db.project_schema import PROJECT_STATUS_ARCHIVED, ProjectRow
from db.project_store import ProjectStore
from db.session_store import SessionStore


def assert_project_access(
    project_id: int,
    current_user_id: int,
    permission: str | None = None,
) -> ProjectRow:
    """校验项目存在（permission 参数保留以兼容调用方，单用户模式下忽略）。"""
    del current_user_id, permission
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="project_id 必须为正整数")
    row, err = ProjectStore.get_project(project_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询项目失败: {err}")
    if not row:
        raise HTTPException(status_code=404, detail="project_id 不存在")
    return row


def assert_project_manage_access(project_id: int, current_user_id: int) -> ProjectRow:
    """校验项目存在且可管理（单用户模式下与 assert_project_access 等价）。"""
    return assert_project_access(project_id, current_user_id)


def assert_project_not_archived(project: ProjectRow) -> None:
    """归档项目禁止写操作。"""
    status = str(project.get("status") or "").strip().lower()
    if status == PROJECT_STATUS_ARCHIVED:
        raise HTTPException(
            status_code=403,
            detail={"code": 8, "msg": "forbidden: project is archived"},
        )


def assert_session_project_not_archived(session_user: dict) -> None:
    """若会话关联项目且已归档，则禁止写操作。"""
    project_id = session_user.get("project_id")
    if not project_id:
        return
    row, err = ProjectStore.get_project(int(project_id))
    if err:
        raise HTTPException(status_code=500, detail=f"查询项目失败: {err}")
    if row:
        assert_project_not_archived(row)


def assert_session_access(
    session_id: str,
    current_user_id: int,
    permission: str | None = None,
) -> SessionUserRow:
    """校验 session 存在（permission 参数保留以兼容调用方，单用户模式下忽略）。"""
    del current_user_id, permission
    sid = session_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    session_user, err = SessionStore.get_session_user(sid)
    if err:
        raise HTTPException(status_code=500, detail=f"查询会话失败: {err}")
    if not session_user:
        raise HTTPException(status_code=404, detail="session_id 不存在，请先创建会话")
    return session_user
