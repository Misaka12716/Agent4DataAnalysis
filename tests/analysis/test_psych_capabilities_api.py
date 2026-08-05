"""2.1.4 模块10：capabilities 管理与编排。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error

SAMPLE_CAP = {
    "capability_id": "stats.describe_full",
    "kind": "stats",
    "enabled": True,
    "version": "2026.Q2",
}


def test_list_capabilities_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_capability_service.list_caps",
        return_value=([SAMPLE_CAP], None),
    ) as mock_list:
        data = assert_success(
            psych_client.get("/psych/capabilities?kind=stats", headers=auth_headers)
        )
    assert data["capabilities"][0]["capability_id"] == "stats.describe_full"
    assert mock_list.call_args[1]["kind"] == "stats"


def test_list_capabilities_db_error(psych_client, auth_headers):
    with patch(
        "backend.psych_capability_service.list_caps",
        return_value=(None, "db"),
    ):
        r = psych_client.get("/psych/capabilities", headers=auth_headers)
    assert r.status_code == 500


def test_list_capabilities_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/capabilities"))


def test_update_capability_ok(psych_client, auth_headers):
    updated = {**SAMPLE_CAP, "enabled": False}
    with patch(
        "backend.psych_capability_service.update_cap",
        return_value=(updated, None),
    ):
        data = assert_success(
            psych_client.put(
                "/psych/capabilities/stats.describe_full",
                headers=auth_headers,
                json={"enabled": False},
            )
        )
    assert data["enabled"] is False


def test_update_capability_error(psych_client, auth_headers):
    with patch(
        "backend.psych_capability_service.update_cap",
        return_value=(None, "能力不存在"),
    ):
        r = psych_client.put(
            "/psych/capabilities/missing",
            headers=auth_headers,
            json={"enabled": True},
        )
    assert r.status_code == 400


def test_update_capability_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.put("/psych/capabilities/x", json={"enabled": True})
    )


def test_compose_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_capability_service.compose",
        return_value=({"pipeline_id": 9, "name": "组合管线"}, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/capabilities/compose",
                headers=auth_headers,
                json={
                    "capability_ids": ["stats.describe_full", "ml.logistic_regression"],
                    "name": "组合管线",
                },
            ),
            status_code=201,
        )
    assert data["pipeline_id"] == 9


def test_compose_missing_ids(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/capabilities/compose",
        headers=auth_headers,
        json={"name": "x"},
    )
    assert_validation_error(r)


def test_compose_error(psych_client, auth_headers):
    with patch(
        "backend.psych_capability_service.compose",
        return_value=(None, "能力不可用"),
    ):
        r = psych_client.post(
            "/psych/capabilities/compose",
            headers=auth_headers,
            json={"capability_ids": ["bad"]},
        )
    assert r.status_code == 400


def test_compose_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post(
            "/psych/capabilities/compose",
            json={"capability_ids": ["a"]},
        )
    )
