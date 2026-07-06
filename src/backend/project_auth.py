"""项目与会话访问校验（RBAC）。"""

from __future__ import annotations

from fastapi import HTTPException

from db.models import SessionUserRow
from db.project_schema import PROJECT_STATUS_ARCHIVED, ProjectRow
from db.project_store import ProjectStore
from db.session_store import SessionStore
from backend.permission_service import (
    can_manage_members,
    can_manage_project,
    has_project_permission,
    is_default_project,
    is_platform_admin,
)


def _forbidden_access() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"code": 7, "msg": "forbidden: project access denied"},
    )


def _forbidden_permission() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"code": 9, "msg": "forbidden: insufficient permission"},
    )


def _forbidden_admin() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"code": 9, "msg": "forbidden: admin required"},
    )


def assert_platform_admin(user_id: int) -> None:
    admin, err = is_platform_admin(user_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询用户失败: {err}")
    if not admin:
        raise _forbidden_admin()


def assert_project_owner(project_id: int, current_user_id: int) -> ProjectRow:
    """校验 project 存在且属于当前用户（保留向后兼容）。"""
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="project_id 必须为正整数")
    row, err = ProjectStore.get_project(project_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询项目失败: {err}")
    if not row:
        raise HTTPException(status_code=404, detail="project_id 不存在")
    owner_id = int(row.get("user_id") or 0)
    if owner_id != current_user_id:
        raise _forbidden_access()
    return row


def assert_project_access(
    project_id: int,
    current_user_id: int,
    permission: str | None = None,
) -> ProjectRow:
    """校验用户对项目的读/写权限。"""
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="project_id 必须为正整数")
    row, err = ProjectStore.get_project(project_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询项目失败: {err}")
    if not row:
        raise HTTPException(status_code=404, detail="project_id 不存在")

    allowed, err = has_project_permission(project_id, current_user_id, permission, row)
    if err:
        raise HTTPException(status_code=500, detail=f"权限校验失败: {err}")
    if not allowed:
        if permission is None:
            raise _forbidden_access()
        raise _forbidden_permission()
    return row


def assert_project_manage_access(project_id: int, current_user_id: int) -> ProjectRow:
    """校验用户可管理项目生命周期（重命名、归档、恢复）。"""
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="project_id 必须为正整数")
    row, err = ProjectStore.get_project(project_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询项目失败: {err}")
    if not row:
        raise HTTPException(status_code=404, detail="project_id 不存在")

    allowed, err = can_manage_project(project_id, current_user_id, row)
    if err:
        raise HTTPException(status_code=500, detail=f"权限校验失败: {err}")
    if not allowed:
        raise _forbidden_permission()
    return row


def assert_member_manage_access(project_id: int, current_user_id: int) -> ProjectRow:
    """校验用户可管理项目成员。"""
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="project_id 必须为正整数")
    row, err = ProjectStore.get_project(project_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询项目失败: {err}")
    if not row:
        raise HTTPException(status_code=404, detail="project_id 不存在")
    if is_default_project(row):
        raise HTTPException(status_code=400, detail="个人默认项目不支持成员管理")

    allowed, err = can_manage_members(project_id, current_user_id, row)
    if err:
        raise HTTPException(status_code=500, detail=f"权限校验失败: {err}")
    if not allowed:
        raise _forbidden_permission()
    return row


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
    """校验 session 存在且当前用户有访问权限。"""
    sid = session_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    session_user, err = SessionStore.get_session_user(sid)
    if err:
        raise HTTPException(status_code=500, detail=f"查询会话失败: {err}")
    if not session_user:
        raise HTTPException(status_code=404, detail="session_id 不存在，请先创建会话")

    owner_id = int(session_user.get("user_id") or 0)
    if owner_id == current_user_id:
        if permission:
            project_id = session_user.get("project_id")
            if project_id:
                assert_project_access(int(project_id), current_user_id, permission)
        return session_user

    project_id = session_user.get("project_id")
    if project_id:
        assert_project_access(int(project_id), current_user_id, permission)
        return session_user

    raise HTTPException(
        status_code=403,
        detail={"code": 7, "msg": "forbidden: session access denied"},
    )
