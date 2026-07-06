"""会话列表与项目生命周期权限测试。"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

if "utils.mysql_utils" not in sys.modules:
    _mysql_mod = MagicMock()
    _mysql_mod.mysql_handler = MagicMock()
    sys.modules["utils.mysql_utils"] = _mysql_mod

from backend.project_auth import assert_project_manage_access
from backend.permission_service import can_manage_project
from db.rbac_schema import PROJECT_ROLE_MANAGER


@patch("db.session_store.SessionStore.get_sessions_by_user_id")
@patch("db.rbac_store.RbacStore.list_projects_for_user")
@patch("db.project_store.ProjectStore.list_sessions_by_project")
def test_get_accessible_sessions_merges_shared(mock_proj_sessions, mock_list_projects, mock_own):
    from db.session_store import SessionStore

    mock_own.return_value = (
        [{"session_id": "s-own", "title": "我的会话"}],
        None,
    )
    mock_list_projects.return_value = (
        [{"id": 1, "user_id": 10, "name": "Demo"}],
        None,
    )
    mock_proj_sessions.return_value = (
        [
            {"session_id": "s-own", "title": "我的会话", "project_id": 1},
            {"session_id": "s-shared", "title": "同事会话", "project_id": 1},
        ],
        None,
    )

    sessions, err = SessionStore.get_accessible_sessions(20)
    assert err is None
    assert len(sessions) == 2
    by_id = {s["session_id"]: s for s in sessions}
    assert by_id["s-own"]["access"] == "owner"
    assert by_id["s-shared"]["access"] == "shared"
    assert by_id["s-shared"]["project_id"] == 1


@patch("backend.permission_service.RbacStore.get_member")
@patch("backend.permission_service.is_platform_admin", return_value=(False, None))
@patch("db.project_store.ProjectStore.get_project")
def test_can_manage_project_manager(mock_get_project, mock_is_admin, mock_get_member):
    mock_get_project.return_value = (
        {"id": 1, "user_id": 10, "name": "Demo", "status": "active"},
        None,
    )
    mock_get_member.return_value = (
        {"role": PROJECT_ROLE_MANAGER, "permissions": []},
        None,
    )
    allowed, err = can_manage_project(1, 20)
    assert err is None
    assert allowed is True


@patch("backend.project_auth.can_manage_project")
@patch("db.project_store.ProjectStore.get_project")
def test_assert_project_manage_access_denied(mock_get_project, mock_can_manage):
    mock_get_project.return_value = (
        {"id": 1, "user_id": 10, "name": "Demo", "status": "active"},
        None,
    )
    mock_can_manage.return_value = (False, None)
    with pytest.raises(HTTPException) as exc:
        assert_project_manage_access(1, 99)
    assert exc.value.status_code == 403
    detail = exc.value.detail
    assert detail.get("code") == 9


@patch("backend.project_auth.can_manage_project")
@patch("db.project_store.ProjectStore.get_project")
def test_assert_project_manage_access_allowed(mock_get_project, mock_can_manage):
    row = {"id": 1, "user_id": 10, "name": "Demo", "status": "active"}
    mock_get_project.return_value = (row, None)
    mock_can_manage.return_value = (True, None)
    result = assert_project_manage_access(1, 20)
    assert result["id"] == 1


@patch("db.rbac_store.RbacStore.get_user")
@patch("db.project_store.ProjectStore.list_all")
def test_list_projects_for_user_admin(mock_list_all, mock_get_user):
    from db.rbac_store import RbacStore

    mock_get_user.return_value = (
        {"id": 1, "platform_role": "admin", "status": "active"},
        None,
    )
    mock_list_all.return_value = (
        [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        None,
    )
    rows, err = RbacStore.list_projects_for_user(1)
    assert err is None
    assert len(rows) == 2
    mock_list_all.assert_called_once()
