"""Project API 集成测试（轻量 FastAPI app + Store mock）。"""

import io
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.testclient import TestClient

# 在导入 route_services / session_memory 之前注入 reader.agent stub
_reader_agent = MagicMock()
_reader_agent.run_workspace_reader_with_markdown_sync = MagicMock(return_value="")
sys.modules.setdefault("reader.agent", _reader_agent)

from backend.api_models import CreateSessionRequest
from backend.jwt_auth import CurrentUser, create_access_token, get_current_user
from backend.project_routes import register_project_routes
from backend.route_services import build_create_session_response, handle_session_upload_excel


def _make_app() -> FastAPI:
    app = FastAPI()
    register_project_routes(app)

    @app.post("/session/create")
    async def create_session(
        body: CreateSessionRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        return build_create_session_response(current_user.user_id, body.project_id)

    @app.post("/session/upload-excel")
    async def session_upload_excel(
        file: UploadFile = File(...),
        session_id: str = Form(...),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        return await handle_session_upload_excel(file, session_id, current_user.user_id)

    return app


@pytest.fixture
def auth_headers():
    token, _ = create_access_token(10, "tester", "13800138000")
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
    with patch("db.project_store._ensure_tables", return_value=(True, None)):
        yield TestClient(_make_app())


@pytest.fixture
def mock_stores(isolated_workspaces):
    project_root = isolated_workspaces / "10" / "1"
    project_root.mkdir(parents=True, exist_ok=True)
    for sub in ("raw", "processed", "outputs", "archive", "sessions"):
        (project_root / sub).mkdir(exist_ok=True)

    active = {
        "id": 1,
        "user_id": 10,
        "name": "Demo",
        "status": "active",
        "workspace_abs_path": str(project_root),
    }
    archived = {**active, "status": "archived"}

    patches = [
        patch("db.project_store.ProjectStore.ensure_schema", return_value=(True, None)),
        patch("db.session_store.SessionStore.user_exists", return_value=(True, None)),
        patch("db.project_store.ProjectStore.get_project"),
        patch("db.project_store.ProjectStore.list_by_user"),
        patch("db.project_store.ProjectStore.create_project"),
        patch("db.project_store.ProjectStore.set_workspace_path", return_value=(True, None)),
        patch("db.project_store.ProjectStore.set_status", return_value=(True, None)),
        patch("db.project_store.ProjectStore.list_assets", return_value=([], None)),
        patch("db.project_store.ProjectStore.create_asset", return_value=(1, None)),
        patch(
            "db.project_store.ProjectStore.list_sessions_by_project",
            return_value=([{"session_id": "sid-1", "title": "T", "project_id": 1}], None),
        ),
        patch("db.project_store.ProjectStore.count_sessions_by_project", return_value=(1, None)),
        patch("db.session_store.SessionStore.get_session_user"),
        patch("db.session_store.SessionStore.create_session", return_value=(True, None)),
        patch("db.rbac_store.RbacStore.list_member_project_ids", return_value=([], None)),
        patch(
            "backend.project_service.ProjectService.ensure_default_project",
            return_value=({"id": 99, "name": "个人默认", "is_default": True}, None),
        ),
        patch(
            "backend.permission_service.get_effective_project_permissions",
            side_effect=lambda pid, uid, row=None: (
                (
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
                )
                if int((row or {}).get("user_id") or 0) == uid
                else (set(), "none", None)
            ),
        ),
    ]
    started = [p.start() for p in patches]
    mocks = {
        "get_project": started[2],
        "list_by_user": started[3],
        "create_project": started[4],
        "list_assets": started[7],
        "get_session_user": started[10],
    }
    mocks["get_project"].side_effect = lambda pid: (active if pid == 1 else None, None)
    mocks["list_by_user"].return_value = ([active], None)
    mocks["create_project"].return_value = (1, None)

    legacy_session = {
        "session_id": "legacy-sid",
        "user_id": 10,
        "project_id": None,
        "workspace_abs_path": str(isolated_workspaces / "10" / "legacy-sid"),
    }

    def _get_session_user(sid):
        if sid == "legacy-sid":
            return legacy_session, None
        return None, None

    mocks["get_session_user"].side_effect = _get_session_user

    yield mocks, active, archived, project_root

    for p in patches:
        p.stop()


def test_project_create_list_get(client, auth_headers, mock_stores):
    _, _active, _, project_root = mock_stores
    with patch("backend.project_service.init_project_workspace", return_value=str(project_root)):
        r = client.post("/project/create", json={"name": "Demo"}, headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["data"]["name"] == "Demo"

    r = client.get("/project/list", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["data"]["projects"]) >= 1

    r = client.get("/project/1", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["id"] == 1


def test_archive_blocks_session_create(client, auth_headers, mock_stores):
    mocks, _active, archived, _ = mock_stores
    mocks["get_project"].side_effect = lambda pid: (archived, None)

    r = client.post("/session/create", json={"project_id": 1}, headers=auth_headers)
    assert r.status_code == 403


def test_restore_allows_session_create(client, auth_headers, mock_stores):
    mocks, active, _archived, project_root = mock_stores
    mocks["get_project"].side_effect = lambda pid: (active, None)

    with patch("backend.route_services.init_workspace") as mock_init, patch(
        "backend.route_services.ensure_runtime"
    ), patch("backend.route_services.persist_workspace_snapshot"):
        mock_init.return_value = str(project_root / "sessions" / "new-sid")
        r = client.post("/session/create", json={"project_id": 1}, headers=auth_headers)
    assert r.status_code == 200
    assert "session_id" in r.json()["data"]


def test_session_create_without_project_id_uses_default(client, auth_headers, mock_stores):
    mocks, active, _, project_root = mock_stores
    mocks["get_project"].side_effect = lambda pid: (active, None)
    with patch(
        "backend.project_service.ProjectService.resolve_project_id", return_value=(1, None)
    ), patch("backend.route_services.init_workspace") as mock_init, patch(
        "backend.route_services.ensure_runtime"
    ), patch("backend.route_services.persist_workspace_snapshot"):
        mock_init.return_value = str(project_root / "sessions" / "new-sid")
        r = client.post("/session/create", json={}, headers=auth_headers)
    assert r.status_code == 200
    assert "session_id" in r.json()["data"]
    mock_init.assert_called_once()
    call_kwargs = mock_init.call_args
    assert call_kwargs[0][0] == 10  # user_id from auth fixture
    assert call_kwargs[1].get("project_id") == 1


def test_project_upload_and_assets(client, auth_headers, mock_stores):
    mocks, active, _, _ = mock_stores
    mocks["get_project"].side_effect = lambda pid: (active, None)
    r = client.post(
        "/project/1/upload",
        headers=auth_headers,
        files={"file": ("报告.csv", io.BytesIO(b"col1\n1\n"), "text/csv")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["relative_path"] == "raw/报告.csv"
    assert body["original_filename"] == "报告.csv"
    assert body.get("renamed") is False

    mocks["list_assets"].return_value = (
        [
            {
                "id": 1,
                "project_id": 1,
                "asset_type": "upload",
                "relative_path": "raw/报告.csv",
                "original_filename": "报告.csv",
            }
        ],
        None,
    )
    r = client.get("/project/1/assets", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["data"]["assets"]) == 1


def test_legacy_session_upload_keeps_original_name(client, auth_headers, mock_stores, isolated_workspaces):
    mocks, _active, _archived, _ = mock_stores
    legacy_path = isolated_workspaces / "10" / "legacy-sid"
    legacy_path.mkdir(parents=True, exist_ok=True)
    legacy_session = {
        "session_id": "legacy-sid",
        "user_id": 10,
        "project_id": None,
        "workspace_abs_path": str(legacy_path),
    }

    with patch("db.session_store.SessionStore.get_session_user", return_value=(legacy_session, None)), patch(
        "backend.route_services.write_bytes_file", return_value=True
    ), patch("backend.route_services.ensure_runtime"), patch(
        "backend.route_services.persist_workspace_snapshot"
    ):
        r = client.post(
            "/session/upload-excel",
            headers=auth_headers,
            data={"session_id": "legacy-sid"},
            files={"file": ("demo.csv", io.BytesIO(b"a,b\n1,2"), "text/csv")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["relative_path"] == "demo.csv"
    assert body["original_filename"] == "demo.csv"
    assert body.get("renamed") is False


def test_legacy_session_upload_renames_on_conflict(client, auth_headers, mock_stores, isolated_workspaces):
    mocks, _active, _archived, _ = mock_stores
    legacy_path = isolated_workspaces / "10" / "legacy-sid-conflict"
    legacy_path.mkdir(parents=True, exist_ok=True)
    (legacy_path / "demo.csv").write_bytes(b"a,b\n0,0")
    legacy_session = {
        "session_id": "legacy-sid-conflict",
        "user_id": 10,
        "project_id": None,
        "workspace_abs_path": str(legacy_path),
    }

    with patch("db.session_store.SessionStore.get_session_user", return_value=(legacy_session, None)), patch(
        "backend.route_services.write_bytes_file", return_value=True
    ), patch("backend.route_services.ensure_runtime"), patch(
        "backend.route_services.persist_workspace_snapshot"
    ):
        r = client.post(
            "/session/upload-excel",
            headers=auth_headers,
            data={"session_id": "legacy-sid-conflict"},
            files={"file": ("demo.csv", io.BytesIO(b"a,b\n1,2"), "text/csv")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["relative_path"] == "demo (1).csv"
    assert body["original_filename"] == "demo.csv"
    assert body.get("renamed") is True


def test_legacy_session_upload_allowed(client, auth_headers, mock_stores, isolated_workspaces):
    mocks, _active, _archived, _ = mock_stores
    legacy_path = isolated_workspaces / "10" / "legacy-sid"
    legacy_path.mkdir(parents=True, exist_ok=True)
    legacy_session = {
        "session_id": "legacy-sid",
        "user_id": 10,
        "project_id": None,
        "workspace_abs_path": str(legacy_path),
    }

    with patch("db.session_store.SessionStore.get_session_user", return_value=(legacy_session, None)), patch(
        "backend.route_services.write_bytes_file", return_value=True
    ), patch("backend.route_services.ensure_runtime"), patch(
        "backend.route_services.persist_workspace_snapshot"
    ):
        r = client.post(
            "/session/upload-excel",
            headers=auth_headers,
            data={"session_id": "legacy-sid"},
            files={"file": ("data.csv", io.BytesIO(b"a,b\n1,2"), "text/csv")},
        )
    assert r.status_code == 200

def test_project_sessions_list(client, auth_headers, mock_stores):
    r = client.get("/project/1/sessions", headers=auth_headers)
    assert r.status_code == 200
    sessions = r.json()["data"]["sessions"]
    assert sessions[0]["session_id"] == "sid-1"


def test_project_archive_restore(client, auth_headers, mock_stores):
    mocks, active, archived, project_root = mock_stores
    mocks["get_project"].side_effect = lambda pid: (active, None)
    with patch(
        "backend.project_lifecycle.snapshot_project_on_archive",
        return_value="archive/20260705-120000",
    ):
        r = client.post("/project/1/archive", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"].get("archive_snapshot_path") == "archive/20260705-120000"

    mocks["get_project"].side_effect = lambda pid: (archived, None)
    r = client.post("/project/1/restore", headers=auth_headers)
    assert r.status_code == 200


def test_project_rename(client, auth_headers, mock_stores):
    mocks, active, _, _ = mock_stores
    mocks["get_project"].side_effect = lambda pid: (active, None)
    with patch("db.project_store.ProjectStore.update_name", return_value=(True, None)):
        r = client.put("/project/1", json={"name": "Renamed"}, headers=auth_headers)
    assert r.status_code == 200


def test_project_tree(client, auth_headers, mock_stores, isolated_workspaces):
    mocks, active, _, project_root = mock_stores
    mocks["get_project"].side_effect = lambda pid: (active, None)
    (project_root / "raw" / "data.csv").write_text("a", encoding="utf-8")
    r = client.get("/project/1/tree", headers=auth_headers)
    assert r.status_code == 200
    trees = r.json()["data"]["trees"]
    assert "raw" in trees
    assert "outputs" in trees
    assert "archive" in trees
