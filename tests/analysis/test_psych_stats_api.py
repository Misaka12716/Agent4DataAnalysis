"""2.1.4 模块3：一键统计分析。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error


def test_stats_methods_ok(psych_client, auth_headers):
    methods = [
        {"method_id": "describe_full", "name_zh": "描述统计"},
        {"method_id": "pearson_correlation", "name_zh": "Pearson"},
    ]
    with patch(
        "backend.psych_stats_service.get_methods",
        return_value=methods,
    ):
        data = assert_success(
            psych_client.get("/psych/stats/methods", headers=auth_headers)
        )
    assert len(data["methods"]) >= 2
    assert data["methods"][0]["method_id"] == "describe_full"


def test_stats_methods_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/stats/methods"))


def test_stats_run_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_stats_service.run_stats",
        return_value=({"task_id": "task_stats_1", "status": "pending"}, None),
    ) as mock_run:
        data = assert_success(
            psych_client.post(
                "/psych/stats/run",
                headers=auth_headers,
                json={
                    "method_ids": ["describe_full", "pearson_correlation"],
                    "dataset_id": 1,
                },
            ),
            status_code=201,
        )
    assert data["task_id"] == "task_stats_1"
    assert mock_run.call_args[0][1] == ["describe_full", "pearson_correlation"]


def test_stats_run_empty_methods(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/stats/run",
        headers=auth_headers,
        json={"method_ids": [], "dataset_id": 1},
    )
    assert_validation_error(r)


def test_stats_run_missing_methods(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/stats/run",
        headers=auth_headers,
        json={"dataset_id": 1},
    )
    assert_validation_error(r)


def test_stats_run_business_error(psych_client, auth_headers):
    with patch(
        "backend.psych_stats_service.run_stats",
        return_value=(None, "数据集不存在"),
    ):
        r = psych_client.post(
            "/psych/stats/run",
            headers=auth_headers,
            json={"method_ids": ["describe_full"], "dataset_id": 99},
        )
    assert r.status_code == 400


def test_stats_run_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post("/psych/stats/run", json={"method_ids": ["describe_full"]})
    )


def test_stats_results_ok(psych_client, auth_headers):
    result = {
        "task_id": "task_stats_1",
        "batch": {"ok_count": 1},
        "method_ids": ["describe_full"],
    }
    with patch(
        "backend.psych_stats_service.get_stats_results",
        return_value=(result, None),
    ):
        data = assert_success(
            psych_client.get("/psych/stats/results/task_stats_1", headers=auth_headers)
        )
    assert data["task_id"] == "task_stats_1"


def test_stats_results_not_found(psych_client, auth_headers):
    with patch(
        "backend.psych_stats_service.get_stats_results",
        return_value=(None, "任务不存在"),
    ):
        r = psych_client.get("/psych/stats/results/missing", headers=auth_headers)
    assert r.status_code == 404


def test_stats_results_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/stats/results/task_x"))
