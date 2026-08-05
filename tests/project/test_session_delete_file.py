"""会话级工作区文件删除接口测试。"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

_reader_agent = MagicMock()
_reader_agent.run_workspace_reader_with_markdown_sync = MagicMock(return_value="")
sys.modules.setdefault("reader.agent", _reader_agent)

from backend.api_models import DeleteSessionFileRequest
from backend.jwt_auth import CurrentUser, create_access_token, get_current_user
from backend.route_services import handle_session_delete_file
from runtime.factory import clear_runtime_cache


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.delete("/session/workspace-file")
    async def session_delete_workspace_file(
        body: DeleteSessionFileRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        return handle_session_delete_file(
            body.session_id,
            body.relative_path,
            current_user.user_id,
        )

    return app


@pytest.fixture
def auth_headers():
    token, _ = create_access_token(10, "tester", "13800138000")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def member_auth_headers():
    token, _ = create_access_token(20, "member", "13800138001")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_rbac_user():
    user_row = {
        "id": 10,
        "username": "tester",
        "phone": "13800138000",
        "platform_role": "user",
        "status": "active",
    }
    with patch("db.rbac_store.RbacStore.get_user", return_value=(user_row, None)):
        yield user_row


@pytest.fixture
def client(isolated_workspaces, mock_rbac_user):
    yield TestClient(_make_app())


@pytest.fixture
def session_workspace(isolated_workspaces):
    project_root = isolated_workspaces / "10" / "1"
    session_root = project_root / "sessions" / "sid-del"
    session_root.mkdir(parents=True, exist_ok=True)
    for sub in ("raw", "processed", "outputs", "archive", "sessions"):
        (project_root / sub).mkdir(exist_ok=True)

    active = {
        "id": 1,
        "user_id": 10,
        "name": "Demo",
        "status": "active",
        "workspace_abs_path": str(project_root),
    }
    session = {
        "session_id": "sid-del",
        "user_id": 10,
        "project_id": 1,
        "workspace_abs_path": str(session_root),
    }
    return project_root, session_root, active, session


@pytest.fixture
def patch_access(session_workspace):
    _project_root, session_root, active, session = session_workspace
    patches = [
        patch("db.session_store.SessionStore.get_session_user", return_value=(session, None)),
        patch(
            "db.session_store.SessionStore.get_workspace_path",
            return_value=str(session_root),
        ),
        patch("db.project_store.ProjectStore.get_project", return_value=(active, None)),
        patch(
            "backend.route_services.resolve_project_root",
            return_value=str(_project_root),
        ),
        patch(
            "backend.permission_service.get_effective_project_permissions",
            return_value=(
                {
                    "data_upload",
                    "data_delete",
                    "data_download",
                    "data_annotate",
                    "data_review",
                    "analysis_create",
                    "training_create",
                    "member_manage",
                },
                "owner",
                None,
            ),
        ),
        patch("backend.route_services.persist_workspace_snapshot"),
        patch("backend.route_services._schedule_workspace_snapshot"),
    ]
    for p in patches:
        p.start()
    yield active, session
    for p in patches:
        p.stop()
    clear_runtime_cache("sid-del")


def test_delete_session_file_success(client, auth_headers, session_workspace, patch_access):
    _project_root, session_root, _active, _session = session_workspace
    target = session_root / "demo.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")

    with patch(
        "backend.route_services.RbacStore.delete_asset", return_value=(True, None)
    ) as mock_del:
        r = client.request(
            "DELETE",
            "/session/workspace-file",
            headers=auth_headers,
            json={"session_id": "sid-del", "relative_path": "demo.csv"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["relative_path"] == "demo.csv"
    assert not target.exists()
    mock_del.assert_called_once()
    args = mock_del.call_args[0]
    assert args[0] == 1
    assert "demo.csv" in args[1]


def test_delete_session_file_not_found(client, auth_headers, session_workspace, patch_access):
    r = client.request(
        "DELETE",
        "/session/workspace-file",
        headers=auth_headers,
        json={"session_id": "sid-del", "relative_path": "missing.csv"},
    )
    assert r.status_code == 404


def test_delete_session_file_path_traversal(client, auth_headers, session_workspace, patch_access):
    r = client.request(
        "DELETE",
        "/session/workspace-file",
        headers=auth_headers,
        json={"session_id": "sid-del", "relative_path": "../raw/secret.csv"},
    )
    assert r.status_code == 400


def test_delete_session_file_rejects_session_memory(
    client, auth_headers, session_workspace, patch_access
):
    _project_root, session_root, _active, _session = session_workspace
    (session_root / "SESSION_MEMORY.md").write_text("# mem\n", encoding="utf-8")

    r = client.request(
        "DELETE",
        "/session/workspace-file",
        headers=auth_headers,
        json={"session_id": "sid-del", "relative_path": "SESSION_MEMORY.md"},
    )
    assert r.status_code == 400
    assert (session_root / "SESSION_MEMORY.md").exists()


def test_delete_session_file_rejects_directory(
    client, auth_headers, session_workspace, patch_access
):
    _project_root, session_root, _active, _session = session_workspace
    (session_root / "subdir").mkdir()

    r = client.request(
        "DELETE",
        "/session/workspace-file",
        headers=auth_headers,
        json={"session_id": "sid-del", "relative_path": "subdir"},
    )
    assert r.status_code == 400


def test_delete_session_file_forbidden_without_data_delete(
    client, member_auth_headers, session_workspace
):
    _project_root, session_root, active, session = session_workspace
    target = session_root / "demo.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")

    member_user = {
        "id": 20,
        "username": "member",
        "phone": "13800138001",
        "platform_role": "user",
        "status": "active",
    }
    with patch("db.rbac_store.RbacStore.get_user", return_value=(member_user, None)), patch(
        "db.session_store.SessionStore.get_session_user", return_value=(session, None)
    ), patch("db.project_store.ProjectStore.get_project", return_value=(active, None)), patch(
        "backend.permission_service.get_effective_project_permissions",
        return_value=({"data_upload"}, "member", None),
    ), patch("backend.route_services._schedule_workspace_snapshot"):
        r = client.request(
            "DELETE",
            "/session/workspace-file",
            headers=member_auth_headers,
            json={"session_id": "sid-del", "relative_path": "demo.csv"},
        )
    assert r.status_code == 403
    assert target.exists()
    clear_runtime_cache("sid-del")
