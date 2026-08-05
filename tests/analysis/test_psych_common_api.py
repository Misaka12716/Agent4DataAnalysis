"""2.1.4 公共接口：health / tasks。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import (
    SAMPLE_PENDING_TASK,
    SAMPLE_TASK,
    assert_success,
    assert_unauthorized,
)


def test_health_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/health"))


def test_health_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_capability_service.health_summary",
        return_value={
            "service": "psych",
            "capabilities_total": 20,
            "capabilities_enabled": 18,
            "by_kind": {"stats": 5, "ml": 10},
        },
    ):
        data = assert_success(psych_client.get("/psych/health", headers=auth_headers))
    assert data["service"] == "psych"
    assert data["capabilities_total"] == 20


def test_list_tasks_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_task_service.list_user_tasks",
        return_value=([SAMPLE_TASK], None),
    ) as mock_list:
        data = assert_success(
            psych_client.get("/psych/tasks?module=stats&limit=10", headers=auth_headers)
        )
    assert len(data["tasks"]) == 1
    mock_list.assert_called_once()
    assert mock_list.call_args[0][0] == 10
    assert mock_list.call_args[1]["module"] == "stats"
    assert mock_list.call_args[1]["limit"] == 10


def test_list_tasks_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/tasks"))


def test_list_tasks_db_error(psych_client, auth_headers):
    with patch(
        "backend.psych_task_service.list_user_tasks",
        return_value=(None, "db down"),
    ):
        r = psych_client.get("/psych/tasks", headers=auth_headers)
    assert r.status_code == 500
    assert "db down" in r.json()["detail"]


def test_get_task_ok(psych_client, auth_headers):
    with patch("backend.psych_task_service.get_task", return_value=(SAMPLE_TASK, None)):
        data = assert_success(
            psych_client.get("/psych/tasks/task_demo_001", headers=auth_headers)
        )
    assert data["task_id"] == "task_demo_001"
    assert data["status"] == "success"


def test_get_task_not_found(psych_client, auth_headers):
    with patch(
        "backend.psych_task_service.get_task",
        return_value=(None, "任务不存在"),
    ):
        r = psych_client.get("/psych/tasks/missing", headers=auth_headers)
    assert r.status_code == 404


def test_get_task_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/tasks/task_x"))


def test_cancel_task_ok(psych_client, auth_headers):
    cancelled = {**SAMPLE_PENDING_TASK, "status": "cancelled"}
    with patch("backend.psych_task_service.cancel_task", return_value=(cancelled, None)):
        data = assert_success(
            psych_client.post("/psych/tasks/task_pending_001/cancel", headers=auth_headers)
        )
    assert data["status"] == "cancelled"


def test_cancel_task_rejected(psych_client, auth_headers):
    with patch(
        "backend.psych_task_service.cancel_task",
        return_value=(None, "无法取消已完成任务"),
    ):
        r = psych_client.post("/psych/tasks/task_demo_001/cancel", headers=auth_headers)
    assert r.status_code == 400
    assert "无法取消" in r.json()["detail"]


def test_cancel_task_requires_auth(psych_client):
    assert_unauthorized(psych_client.post("/psych/tasks/task_x/cancel"))
