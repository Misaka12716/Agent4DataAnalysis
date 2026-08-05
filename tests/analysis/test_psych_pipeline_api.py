"""2.1.4 模块2：pipelines + param-templates。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error


def test_pipeline_methods_ok(psych_client, auth_headers):
    catalog = {"methods": [{"method_id": "describe_full", "name_zh": "描述统计"}]}
    with patch(
        "backend.psych_pipeline_service.list_pipeline_methods",
        return_value=catalog,
    ):
        data = assert_success(
            psych_client.get("/psych/pipelines/methods", headers=auth_headers)
        )
    assert data["methods"][0]["method_id"] == "describe_full"


def test_pipeline_methods_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/pipelines/methods"))


def test_create_pipeline_ok(psych_client, auth_headers):
    created = {"id": 3, "name": "基线管线", "steps": [{"method_id": "describe_full"}]}
    with patch(
        "backend.psych_pipeline_service.create_pipeline",
        return_value=(created, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/pipelines",
                headers=auth_headers,
                json={
                    "name": "基线管线",
                    "steps": [{"method_id": "describe_full"}],
                },
            ),
            status_code=201,
        )
    assert data["id"] == 3


def test_create_pipeline_missing_steps(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/pipelines", headers=auth_headers, json={"name": "x"}
    )
    assert_validation_error(r)


def test_create_pipeline_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post(
            "/psych/pipelines",
            json={"name": "x", "steps": [{"method_id": "describe_full"}]},
        )
    )


def test_list_pipelines_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_pipeline_service.list_pipelines",
        return_value=([{"id": 1, "name": "p1"}], None),
    ):
        data = assert_success(psych_client.get("/psych/pipelines", headers=auth_headers))
    assert data["pipelines"][0]["name"] == "p1"


def test_list_pipelines_db_error(psych_client, auth_headers):
    with patch(
        "backend.psych_pipeline_service.list_pipelines",
        return_value=(None, "db"),
    ):
        r = psych_client.get("/psych/pipelines", headers=auth_headers)
    assert r.status_code == 500


def test_run_pipeline_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_pipeline_service.run_pipeline",
        return_value=({"task_id": "task_pipe_1", "status": "pending"}, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/pipelines/1/run",
                headers=auth_headers,
                json={"dataset_id": 1},
            ),
            status_code=201,
        )
    assert data["task_id"] == "task_pipe_1"


def test_run_pipeline_error(psych_client, auth_headers):
    with patch(
        "backend.psych_pipeline_service.run_pipeline",
        return_value=(None, "管线不存在"),
    ):
        r = psych_client.post(
            "/psych/pipelines/99/run", headers=auth_headers, json={}
        )
    assert r.status_code == 400


def test_run_pipeline_requires_auth(psych_client):
    assert_unauthorized(psych_client.post("/psych/pipelines/1/run", json={}))


def test_save_param_template_ok(psych_client, auth_headers):
    saved = {"id": 2, "module": "stats", "method_id": "describe_full", "name": "默认"}
    with patch(
        "backend.psych_pipeline_service.save_param_template",
        return_value=(saved, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/param-templates",
                headers=auth_headers,
                json={
                    "module": "stats",
                    "method_id": "describe_full",
                    "name": "默认",
                    "params": {"alpha": 0.05},
                    "is_default": True,
                },
            ),
            status_code=201,
        )
    assert data["id"] == 2


def test_save_param_template_missing_fields(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/param-templates",
        headers=auth_headers,
        json={"module": "stats"},
    )
    assert_validation_error(r)


def test_list_param_templates_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_pipeline_service.list_param_templates",
        return_value=([{"id": 1, "name": "默认"}], None),
    ) as mock_list:
        data = assert_success(
            psych_client.get(
                "/psych/param-templates?module=stats", headers=auth_headers
            )
        )
    assert data["templates"][0]["name"] == "默认"
    assert mock_list.call_args[1]["module"] == "stats"


def test_list_param_templates_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/param-templates"))
