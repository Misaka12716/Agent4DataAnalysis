"""RBAC 权限与访问控制测试。"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

if "utils.mysql_utils" not in sys.modules:
    _mysql_mod = MagicMock()
    _mysql_mod.mysql_handler = MagicMock()
    sys.modules["utils.mysql_utils"] = _mysql_mod

from backend.permission_service import (
    get_effective_project_permissions,
    has_project_permission,
    is_user_blocked,
    resolve_member_permissions,
)
from backend.project_auth import assert_platform_admin, assert_project_access, assert_session_access
from db.rbac_schema import (
    PERM_DATA_DELETE,
    PERM_DATA_DOWNLOAD,
    PERM_DATA_UPLOAD,
    PROJECT_ROLE_MANAGER,
    PROJECT_ROLE_MEMBER,
)


def test_is_user_blocked():
    assert is_user_blocked({"status": "blocked"}) is True
    assert is_user_blocked({"status": "active"}) is False
    assert is_user_blocked({"is_blocked": True}) is True


def test_resolve_member_permissions_manager():
    perms = resolve_member_permissions(PROJECT_ROLE_MANAGER, [])
    assert PERM_DATA_UPLOAD in perms
    assert PERM_DATA_DELETE in perms


def test_resolve_member_permissions_custom():
    perms = resolve_member_permissions(PROJECT_ROLE_MEMBER, [PERM_DATA_DOWNLOAD])
    assert PERM_DATA_DOWNLOAD in perms
    assert PERM_DATA_UPLOAD not in perms


@patch("backend.permission_service.RbacStore.get_member")
@patch("backend.permission_service.is_platform_admin", return_value=(False, None))
@patch("db.project_store.ProjectStore.get_project")
def test_member_upload_permission(mock_get_project, mock_is_admin, mock_get_member):
    mock_get_project.return_value = (
        {"id": 1, "user_id": 10, "name": "Demo", "status": "active"},
        None,
    )
    mock_get_member.return_value = (
        {"role": PROJECT_ROLE_MEMBER, "permissions": [PERM_DATA_UPLOAD]},
        None,
    )
    allowed, err = has_project_permission(1, 20, PERM_DATA_UPLOAD)
    assert err is None
    assert allowed is True

    allowed, err = has_project_permission(1, 20, PERM_DATA_DELETE)
    assert allowed is False


@patch("backend.permission_service.RbacStore.get_member")
@patch("backend.permission_service.is_platform_admin", return_value=(False, None))
@patch("db.project_store.ProjectStore.get_project")
def test_owner_has_all_permissions(mock_get_project, mock_is_admin, mock_get_member):
    mock_get_project.return_value = (
        {"id": 1, "user_id": 10, "name": "Demo", "status": "active"},
        None,
    )
    mock_get_member.return_value = (None, None)
    perms, access, err = get_effective_project_permissions(1, 10)
    assert err is None
    assert access == "owner"
    assert PERM_DATA_DELETE in perms


@patch("backend.project_auth.is_platform_admin")
def test_assert_platform_admin_denied(mock_is_admin):
    mock_is_admin.return_value = (False, None)
    with pytest.raises(HTTPException) as exc:
        assert_platform_admin(5)
    assert exc.value.status_code == 403


@patch("backend.project_auth.has_project_permission")
@patch("db.project_store.ProjectStore.get_project")
def test_assert_project_access_denied(mock_get_project, mock_has_perm):
    mock_get_project.return_value = (
        {"id": 1, "user_id": 10, "name": "Demo", "status": "active"},
        None,
    )
    mock_has_perm.return_value = (False, None)
    with pytest.raises(HTTPException) as exc:
        assert_project_access(1, 99, PERM_DATA_UPLOAD)
    assert exc.value.status_code == 403
    detail = exc.value.detail
    assert detail.get("code") == 9


@patch("backend.project_auth.assert_project_access")
@patch("db.session_store.SessionStore.get_session_user")
def test_assert_session_access_via_project_member(mock_get_session, mock_project_access):
    mock_get_session.return_value = (
        {"session_id": "s1", "user_id": 10, "project_id": 1},
        None,
    )
    mock_project_access.return_value = {"id": 1, "user_id": 10}
    row = assert_session_access("s1", 20, PERM_DATA_DOWNLOAD)
    assert row["session_id"] == "s1"
    mock_project_access.assert_called_once_with(1, 20, PERM_DATA_DOWNLOAD)


@patch("backend.permission_service.is_platform_admin", return_value=(True, None))
def test_admin_bypass_permission(mock_is_admin):
    perms, access, err = get_effective_project_permissions(1, 1, {"id": 1, "user_id": 99})
    assert access == "admin"
    assert PERM_DATA_UPLOAD in perms
