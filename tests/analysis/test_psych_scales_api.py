"""2.1.4 模块12：量表结构化与分析。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error

PHQ9_ITEMS = {f"PHQ9_{i}": 1 for i in range(1, 10)}


def test_scale_forms_ok(psych_client, auth_headers):
    forms = [{"scale_code": "PHQ9", "display_name": "PHQ-9", "version": "1.0"}]
    with patch(
        "backend.psych_scale_service.list_forms",
        return_value=(forms, None),
    ):
        data = assert_success(
            psych_client.get("/psych/scales/forms", headers=auth_headers)
        )
    assert data["forms"][0]["scale_code"] == "PHQ9"


def test_scale_forms_db_error(psych_client, auth_headers):
    with patch(
        "backend.psych_scale_service.list_forms",
        return_value=(None, "db"),
    ):
        r = psych_client.get("/psych/scales/forms", headers=auth_headers)
    assert r.status_code == 500


def test_scale_forms_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/scales/forms"))


def test_scale_parse_ok(psych_client, auth_headers):
    parsed = {"scale_code": "PHQ9", "item_scores": PHQ9_ITEMS, "patient_key": "P1"}
    with patch(
        "backend.psych_scale_service.parse_raw",
        return_value=(parsed, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/scales/parse",
                headers=auth_headers,
                json={
                    "scale_code": "PHQ9",
                    "raw": [1] * 9,
                    "patient_key": "P1",
                },
            )
        )
    assert data["item_scores"]["PHQ9_1"] == 1


def test_scale_parse_missing_fields(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/scales/parse",
        headers=auth_headers,
        json={"scale_code": "PHQ9"},
    )
    assert_validation_error(r)


def test_scale_parse_error(psych_client, auth_headers):
    with patch(
        "backend.psych_scale_service.parse_raw",
        return_value=(None, "未知量表"),
    ):
        r = psych_client.post(
            "/psych/scales/parse",
            headers=auth_headers,
            json={"scale_code": "XXX", "raw": []},
        )
    assert r.status_code == 400


def test_scale_parse_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post(
            "/psych/scales/parse",
            json={"scale_code": "PHQ9", "raw": [1]},
        )
    )


def test_scale_score_ok(psych_client, auth_headers):
    scored = {"id": 99, "scale_code": "PHQ9", "total": 9.0, "patient_key": "P1"}
    with patch(
        "backend.psych_scale_service.score",
        return_value=(scored, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/scales/score",
                headers=auth_headers,
                json={
                    "scale_code": "PHQ9",
                    "item_scores": PHQ9_ITEMS,
                    "patient_key": "P1",
                },
            ),
            status_code=201,
        )
    assert data["total"] == 9.0


def test_scale_score_missing_patient_key(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/scales/score",
        headers=auth_headers,
        json={"scale_code": "PHQ9", "item_scores": PHQ9_ITEMS},
    )
    assert_validation_error(r)


def test_scale_score_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post(
            "/psych/scales/score",
            json={
                "scale_code": "PHQ9",
                "item_scores": PHQ9_ITEMS,
                "patient_key": "P1",
            },
        )
    )


def test_scale_scores_list_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_scale_service.list_scores",
        return_value=([{"id": 1, "total": 9.0, "patient_key": "P1"}], None),
    ) as mock_list:
        data = assert_success(
            psych_client.get(
                "/psych/scales/scores?scale_code=PHQ9&patient_key=P1&limit=10",
                headers=auth_headers,
            )
        )
    assert data["scores"][0]["total"] == 9.0
    assert mock_list.call_args[1]["limit"] == 10


def test_scale_scores_list_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/scales/scores"))


def test_scale_trend_ok(psych_client, auth_headers):
    trend = {
        "patient_key": "P1",
        "scale_code": "PHQ9",
        "points": [{"date": "2026-01-01", "total": 15}, {"date": "2026-02-01", "total": 9}],
    }
    with patch(
        "backend.psych_scale_service.trend",
        return_value=(trend, None),
    ):
        data = assert_success(
            psych_client.get(
                "/psych/scales/trend?patient_key=P1&scale_code=PHQ9",
                headers=auth_headers,
            )
        )
    assert len(data["points"]) == 2


def test_scale_trend_missing_query(psych_client, auth_headers):
    r = psych_client.get("/psych/scales/trend", headers=auth_headers)
    assert_validation_error(r)


def test_scale_trend_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.get("/psych/scales/trend?patient_key=P1&scale_code=PHQ9")
    )


def test_scale_compare_ok(psych_client, auth_headers):
    cmp = {
        "scale_code": "PHQ9",
        "group_a_mean": 12.0,
        "group_b_mean": 8.0,
        "p_value": 0.03,
    }
    with patch(
        "backend.psych_scale_service.compare",
        return_value=(cmp, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/scales/compare",
                headers=auth_headers,
                json={
                    "scale_code": "PHQ9",
                    "group_a": ["P1", "P2"],
                    "group_b": ["P3", "P4"],
                },
            )
        )
    assert data["p_value"] == 0.03


def test_scale_compare_missing_groups(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/scales/compare",
        headers=auth_headers,
        json={"scale_code": "PHQ9", "group_a": ["P1"]},
    )
    assert_validation_error(r)


def test_scale_compare_error(psych_client, auth_headers):
    with patch(
        "backend.psych_scale_service.compare",
        return_value=(None, "样本量不足"),
    ):
        r = psych_client.post(
            "/psych/scales/compare",
            headers=auth_headers,
            json={
                "scale_code": "PHQ9",
                "group_a": ["P1"],
                "group_b": ["P2"],
            },
        )
    assert r.status_code == 400


def test_scale_compare_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post(
            "/psych/scales/compare",
            json={"scale_code": "PHQ9", "group_a": [], "group_b": []},
        )
    )


def test_scale_export_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_scale_service.export_scores",
        return_value=({"format": "json", "rows": [{"patient_key": "P1", "total": 9}]}, None),
    ):
        data = assert_success(
            psych_client.get(
                "/psych/scales/export?scale_code=PHQ9&dataset_id=1",
                headers=auth_headers,
            )
        )
    assert data["rows"][0]["total"] == 9


def test_scale_export_error(psych_client, auth_headers):
    with patch(
        "backend.psych_scale_service.export_scores",
        return_value=(None, "无数据"),
    ):
        r = psych_client.get("/psych/scales/export", headers=auth_headers)
    assert r.status_code == 400


def test_scale_export_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/scales/export"))
