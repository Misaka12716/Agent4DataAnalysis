"""模板 API 鉴权与 template-run 集成测试。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_reader_agent = MagicMock()
_reader_agent.run_workspace_reader_with_markdown_sync = MagicMock(return_value="")
sys.modules.setdefault("reader.agent", _reader_agent)

from backend.jwt_auth import create_access_token  # noqa: E402
from backend.route_registry import register_modular_routes  # noqa: E402


def _make_app() -> FastAPI:
    app = FastAPI()
    register_modular_routes(app)
    return app


@pytest.fixture
def user_headers():
    token, _ = create_access_token(10, "tester", "13800138000")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    token, _ = create_access_token(1, "admin", "13800138001")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_user():
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
def mock_admin():
    admin_row = {
        "id": 1,
        "username": "admin",
        "phone": "13800138001",
        "platform_role": "admin",
        "status": "active",
    }
    with patch("db.rbac_store.RbacStore.get_user", return_value=(admin_row, None)):
        yield admin_row


@pytest.fixture
def client(mock_user):
    with patch("backend.permission_service.is_platform_admin", return_value=(False, None)):
        with patch("backend.project_auth.is_platform_admin", return_value=(False, None)):
            yield TestClient(_make_app())


@pytest.fixture
def admin_client(mock_admin):
    with patch("backend.permission_service.is_platform_admin", return_value=(True, None)):
        with patch("backend.project_auth.is_platform_admin", return_value=(True, None)):
            yield TestClient(_make_app())


def test_template_list_requires_auth(client):
    r = client.get("/template/list")
    assert r.status_code == 401


def test_template_list_authenticated(client, user_headers):
    templates = [{"id": 1, "template_name": "抑郁模板", "disease_type": "depression", "version": "1.0.0"}]
    with patch("backend.template_service.TemplateService.list_templates", return_value=(templates, None)):
        r = client.get("/template/list", headers=user_headers)
    assert r.status_code == 200
    assert r.json()["data"][0]["template_name"] == "抑郁模板"


def _valid_create_payload(name: str = "测试模板") -> dict:
    return {
        "template_name": name,
        "disease_type": "depression",
        "scales": ["HAMD-17"],
        "analysis_steps": [{"step": 1, "name": "描述统计", "action": "describe_full"}],
        "report_structure": ["summary"],
    }


def test_template_create_requires_admin(client, user_headers):
    with patch("backend.project_auth.is_platform_admin", return_value=(False, None)):
        r = client.post(
            "/template/create",
            headers=user_headers,
            json=_valid_create_payload(),
        )
    assert r.status_code == 403


def test_template_create_admin_ok(admin_client, admin_headers):
    created = {"id": 2, "template_name": "新模板", "disease_type": "depression"}
    with patch("backend.template_service.TemplateService.create_template", return_value=(created, None)):
        r = admin_client.post(
            "/template/create",
            headers=admin_headers,
            json=_valid_create_payload("新模板"),
        )
    assert r.status_code == 201
    assert r.json()["data"]["id"] == 2


def test_template_run_registers_assets(client, user_headers):
    session_row = {
        "session_id": "sid-tpl",
        "user_id": 10,
        "project_id": 1,
        "workspace_abs_path": "/tmp/ws/sid-tpl",
    }
    analysis_result = {
        "template_id": 1,
        "template_name": "抑郁",
        "row_count": 10,
        "step_results": [],
        "report_markdown": "# report",
    }
    with patch("backend.template_routes.assert_session_access", return_value=session_row):
        with patch("backend.template_routes.assert_session_project_not_archived"):
            with patch(
                "backend.template_analysis_service.run_template_analysis",
                return_value=(analysis_result, None),
            ) as mock_run:
                r = client.post(
                    "/analysis/template-run",
                    headers=user_headers,
                    json={"session_id": "sid-tpl", "template_id": 1},
                )
    assert r.status_code == 200
    mock_run.assert_called_once()
    assert r.json()["data"]["template_name"] == "抑郁"


def test_template_run_requires_auth(client):
    r = client.post("/analysis/template-run", json={"session_id": "sid", "template_id": 1})
    assert r.status_code == 401
