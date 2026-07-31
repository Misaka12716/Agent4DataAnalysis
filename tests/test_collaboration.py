"""项目共享与协同流程测试。"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

if "utils.mysql_utils" not in sys.modules:
    _mysql_mod = MagicMock()
    _mysql_mod.mysql_handler = MagicMock()
    sys.modules["utils.mysql_utils"] = _mysql_mod

from backend.jwt_auth import create_access_token
from backend.member_routes import register_member_routes
from backend.project_routes import register_project_routes
from backend.rbac_models import AddMemberRequest
from fastapi import FastAPI


def _make_app() -> FastAPI:
    app = FastAPI()
    register_project_routes(app)
    register_member_routes(app)
    return app


@pytest.fixture
def owner_headers():
    token, _ = create_access_token(10, "owner", "13800138000")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def member_headers():
    token, _ = create_access_token(20, "collaborator", "13800138001")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_users():
    owner = {
        "id": 10,
        "username": "owner",
        "phone": "13800138000",
        "platform_role": "user",
        "status": "active",
    }
    collaborator = {
        "id": 20,
        "username": "collaborator",
        "phone": "13800138001",
        "platform_role": "user",
        "status": "active",
    }

    def _get_user(uid):
        if uid == 10:
            return owner, None
        if uid == 20:
            return collaborator, None
        return None, None

    def _get_by_phone(phone):
        if phone == "13800138001":
            return collaborator, None
        if phone == "13800138000":
            return owner, None
        return None, None

    with patch("db.rbac_store.RbacStore.get_user", side_effect=_get_user), patch(
        "db.rbac_store.RbacStore.get_user_by_phone", side_effect=_get_by_phone
    ):
        yield owner, collaborator


@pytest.fixture
def client(isolated_workspaces, mock_users):
    project_root = isolated_workspaces / "10" / "1"
    project_root.mkdir(parents=True, exist_ok=True)
    active = {
        "id": 1,
        "user_id": 10,
        "name": "SharedDemo",
        "status": "active",
        "workspace_abs_path": str(project_root),
    }
    member_row = {
        "id": 1,
        "project_id": 1,
        "user_id": 20,
        "role": "member",
        "permissions": ["data_download", "data_upload"],
    }

    with patch("db.project_store._ensure_tables", return_value=(True, None)), patch(
        "db.project_store.ProjectStore.ensure_schema", return_value=(True, None)
    ), patch("db.project_store.ProjectStore.get_project", return_value=(active, None)), patch(
        "db.project_store.ProjectStore.list_by_user", return_value=([active], None)
    ), patch(
        "db.rbac_store.RbacStore.list_member_project_ids", return_value=([1], None)
    ), patch(
        "db.rbac_store.RbacStore.list_projects_for_user", return_value=([active], None)
    ), patch(
        "db.rbac_store.RbacStore.get_member",
        side_effect=lambda pid, uid: (
            (member_row, None) if pid == 1 and uid == 20 else (None, None)
        ),
    ), patch(
        "db.rbac_store.RbacStore.list_members",
        return_value=([{**member_row, "username": "collaborator", "phone": "13800138001"}], None),
    ), patch(
        "db.rbac_store.RbacStore.add_member", return_value=(99, None)
    ), patch(
        "backend.project_service.ProjectService.ensure_default_project",
        return_value=({"id": 2, "name": "个人默认", "is_default": True}, None),
    ), patch(
        "db.project_store.ProjectStore.count_sessions_by_project", return_value=(0, None)
    ):
        yield TestClient(_make_app())


def test_add_member_request_requires_user_id_or_phone():
    with pytest.raises(ValueError):
        AddMemberRequest(user_id=10, phone="13800138001")
    with pytest.raises(ValueError):
        AddMemberRequest()
    req = AddMemberRequest(phone="13800138001")
    assert req.phone == "13800138001"


def test_user_lookup_by_phone(client, owner_headers, mock_users):
    r = client.get("/users/lookup?phone=13800138001", headers=owner_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["user_id"] == 20
    assert data["phone"] == "13800138001"

    r = client.get("/users/lookup?phone=13999999999", headers=owner_headers)
    assert r.status_code == 404


def test_add_member_by_phone(client, owner_headers, mock_users):
    with patch("backend.member_routes.assert_member_manage_access"):
        r = client.post(
            "/project/1/members",
            headers=owner_headers,
            json={
                "phone": "13800138001",
                "role": "member",
                "permissions": ["data_download", "analysis_create"],
            },
        )
    assert r.status_code == 201
    assert r.json()["data"]["user_id"] == 20


def test_add_member_rejects_project_owner(client, owner_headers, mock_users):
    with patch("backend.member_routes.assert_member_manage_access"):
        r = client.post(
            "/project/1/members",
            headers=owner_headers,
            json={
                "phone": "13800138000",
                "role": "member",
                "permissions": ["data_download"],
            },
        )
    assert r.status_code == 400
    assert "创建者" in r.json()["detail"]


def test_member_can_list_members_readonly(client, member_headers, mock_users):
    with patch("backend.member_routes.assert_project_access"):
        r = client.get("/project/1/members", headers=member_headers)
    assert r.status_code == 200
    members = r.json()["data"]["members"]
    assert len(members) == 1
    assert members[0]["user_id"] == 20


def test_enrich_project_access_marks_shared_member():
    from backend.project_service import _enrich_project_access

    row = {"id": 1, "user_id": 10, "name": "SharedDemo", "status": "active"}
    item = {"id": 1, "name": "SharedDemo", "is_default": False}
    with patch(
        "backend.permission_service.get_effective_project_permissions",
        return_value=({"data_download", "data_upload"}, "member", None),
    ), patch(
        "db.rbac_store.RbacStore.get_member",
        return_value=({"role": "member", "permissions": ["data_download", "data_upload"]}, None),
    ):
        enriched = _enrich_project_access(item, 20, row)
    assert enriched["access"] == "member"
    assert enriched["is_shared"] is True
    assert "data_upload" in enriched["permissions"]
