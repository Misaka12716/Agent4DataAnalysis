"""2.1.4 模块6：analysis-params + exports。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error


def test_get_analysis_params_ok(psych_client, auth_headers):
    rows = [{"scope": "stats", "items": {"alpha": 0.05}}]
    with patch(
        "backend.psych_config_export_service.list_params",
        return_value=(rows, None),
    ) as mock_list:
        data = assert_success(
            psych_client.get("/psych/analysis-params?scope=stats", headers=auth_headers)
        )
    assert data["params"][0]["scope"] == "stats"
    assert mock_list.call_args[1]["scope"] == "stats"


def test_get_analysis_params_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/analysis-params"))


def test_get_analysis_params_db_error(psych_client, auth_headers):
    with patch(
        "backend.psych_config_export_service.list_params",
        return_value=(None, "db"),
    ):
        r = psych_client.get("/psych/analysis-params", headers=auth_headers)
    assert r.status_code == 500


def test_put_analysis_params_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_config_export_service.upsert_params",
        return_value=({"scope": "stats", "items": {"alpha": 0.01}}, None),
    ):
        data = assert_success(
            psych_client.put(
                "/psych/analysis-params",
                headers=auth_headers,
                json={"scope": "stats", "items": {"alpha": 0.01}},
            )
        )
    assert data["items"]["alpha"] == 0.01


def test_put_analysis_params_missing_scope(psych_client, auth_headers):
    r = psych_client.put(
        "/psych/analysis-params",
        headers=auth_headers,
        json={"items": {"alpha": 0.05}},
    )
    assert_validation_error(r)


def test_put_analysis_params_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.put(
            "/psych/analysis-params",
            json={"scope": "stats", "items": {}},
        )
    )


def test_create_export_ok(psych_client, auth_headers):
    created = {"export_id": "exp_1", "kind": "stats", "format": "csv"}
    with patch(
        "backend.psych_config_export_service.create_export",
        return_value=(created, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/exports",
                headers=auth_headers,
                json={"kind": "stats", "format": "csv", "task_id": "task_1"},
            ),
            status_code=201,
        )
    assert data["export_id"] == "exp_1"


def test_create_export_missing_kind(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/exports", headers=auth_headers, json={"format": "csv"}
    )
    assert_validation_error(r)


def test_create_export_error(psych_client, auth_headers):
    with patch(
        "backend.psych_config_export_service.create_export",
        return_value=(None, "无导出数据"),
    ):
        r = psych_client.post(
            "/psych/exports",
            headers=auth_headers,
            json={"kind": "stats"},
        )
    assert r.status_code == 400


def test_create_export_requires_auth(psych_client):
    assert_unauthorized(psych_client.post("/psych/exports", json={"kind": "stats"}))


def test_download_export_ok(psych_client, auth_headers, tmp_path):
    fpath = tmp_path / "exp_1.csv"
    fpath.write_text("a,b\n1,2\n", encoding="utf-8")
    with patch(
        "backend.psych_config_export_service.get_export_file",
        return_value=({"file_path": str(fpath), "format": "csv"}, None),
    ):
        r = psych_client.get("/psych/exports/exp_1/download", headers=auth_headers)
    assert r.status_code == 200
    assert "1,2" in r.text or r.content


def test_download_export_not_found(psych_client, auth_headers):
    with patch(
        "backend.psych_config_export_service.get_export_file",
        return_value=(None, "导出不存在"),
    ):
        r = psych_client.get("/psych/exports/missing/download", headers=auth_headers)
    assert r.status_code == 404


def test_download_export_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/exports/exp_1/download"))
