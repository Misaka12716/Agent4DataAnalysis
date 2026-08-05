"""2.1.4 模块4：variables + var-categories。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error

SAMPLE_VAR = {
    "id": 1,
    "var_name": "HAMD_total",
    "display_name": "HAMD总分",
    "dtype": "float",
}


def test_create_variable_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.create_variable",
        return_value=(SAMPLE_VAR, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/variables",
                headers=auth_headers,
                json={"var_name": "HAMD_total", "display_name": "HAMD总分", "dtype": "float"},
            ),
            status_code=201,
        )
    assert data["var_name"] == "HAMD_total"


def test_create_variable_missing_name(psych_client, auth_headers):
    r = psych_client.post("/psych/variables", headers=auth_headers, json={"dtype": "float"})
    assert_validation_error(r)


def test_create_variable_requires_auth(psych_client):
    assert_unauthorized(psych_client.post("/psych/variables", json={"var_name": "x"}))


def test_list_variables_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.list_variables",
        return_value=([SAMPLE_VAR], None),
    ) as mock_list:
        data = assert_success(
            psych_client.get("/psych/variables?dataset_id=1", headers=auth_headers)
        )
    assert data["variables"][0]["id"] == 1
    assert mock_list.call_args[1]["dataset_id"] == 1


def test_list_variables_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/variables"))


def test_update_variable_ok(psych_client, auth_headers):
    updated = {**SAMPLE_VAR, "display_name": "新名称"}
    with patch(
        "backend.psych_variable_service.update_variable",
        return_value=(updated, None),
    ):
        data = assert_success(
            psych_client.put(
                "/psych/variables/1",
                headers=auth_headers,
                json={"display_name": "新名称"},
            )
        )
    assert data["display_name"] == "新名称"


def test_update_variable_error(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.update_variable",
        return_value=(None, "变量不存在"),
    ):
        r = psych_client.put(
            "/psych/variables/99", headers=auth_headers, json={"display_name": "x"}
        )
    assert r.status_code == 400


def test_delete_variable_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.delete_variable",
        return_value=({"deleted": True, "id": 1}, None),
    ):
        data = assert_success(
            psych_client.delete("/psych/variables/1", headers=auth_headers)
        )
    assert data["deleted"] is True


def test_delete_variable_requires_auth(psych_client):
    assert_unauthorized(psych_client.delete("/psych/variables/1"))


def test_batch_variables_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.batch_edit",
        return_value=({"updated": 2}, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/variables/batch",
                headers=auth_headers,
                json={"items": [{"var_name": "a"}, {"var_name": "b"}]},
            )
        )
    assert data["updated"] == 2


def test_batch_variables_missing_items(psych_client, auth_headers):
    r = psych_client.post("/psych/variables/batch", headers=auth_headers, json={})
    assert_validation_error(r)


def test_variable_mapping_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.set_mapping",
        return_value=({"var_id": 1, "mapping": {"col": "HAMD"}}, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/variables/mapping",
                headers=auth_headers,
                json={"var_id": 1, "mapping": {"col": "HAMD"}},
            )
        )
    assert data["var_id"] == 1


def test_variable_mapping_missing_fields(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/variables/mapping",
        headers=auth_headers,
        json={"var_id": 1},
    )
    assert r.status_code == 400
    assert "mapping" in r.json()["detail"]


def test_dictionary_export_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.export_dictionary",
        return_value=({"format": "json", "items": [SAMPLE_VAR]}, None),
    ):
        data = assert_success(
            psych_client.get(
                "/psych/variables/dictionary/export?format=json&dataset_id=1",
                headers=auth_headers,
            )
        )
    assert data["format"] == "json"


def test_dictionary_export_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/variables/dictionary/export"))


def test_create_category_ok(psych_client, auth_headers):
    cat = {"id": 5, "name": "量表", "parent_id": None, "sort_order": 0}
    with patch(
        "backend.psych_variable_service.create_category",
        return_value=(cat, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/var-categories",
                headers=auth_headers,
                json={"name": "量表"},
            ),
            status_code=201,
        )
    assert data["name"] == "量表"


def test_create_category_missing_name(psych_client, auth_headers):
    r = psych_client.post("/psych/var-categories", headers=auth_headers, json={})
    assert_validation_error(r)


def test_list_categories_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.list_categories",
        return_value=([{"id": 1, "name": "量表"}], None),
    ):
        data = assert_success(
            psych_client.get("/psych/var-categories", headers=auth_headers)
        )
    assert data["categories"][0]["name"] == "量表"


def test_update_category_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.update_category",
        return_value=({"id": 1, "name": "测评"}, None),
    ):
        data = assert_success(
            psych_client.put(
                "/psych/var-categories/1",
                headers=auth_headers,
                json={"name": "测评"},
            )
        )
    assert data["name"] == "测评"


def test_delete_category_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.delete_category",
        return_value=({"deleted": True}, None),
    ):
        data = assert_success(
            psych_client.delete("/psych/var-categories/1", headers=auth_headers)
        )
    assert data["deleted"] is True


def test_delete_category_error(psych_client, auth_headers):
    with patch(
        "backend.psych_variable_service.delete_category",
        return_value=(None, "分类不存在"),
    ):
        r = psych_client.delete("/psych/var-categories/99", headers=auth_headers)
    assert r.status_code == 400


def test_categories_require_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/var-categories"))
