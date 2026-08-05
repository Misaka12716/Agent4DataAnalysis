"""2.1.4 模块8：特征挖掘。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error

SAMPLE_FEAT = {
    "id": 3,
    "feature_set_name": "基线特征",
    "feature_type": "statistical",
    "status": "ready",
}


def test_features_extract_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_feature_service.extract_features",
        return_value=({"task_id": "task_feat_1", "status": "pending"}, None),
    ) as mock_ext:
        data = assert_success(
            psych_client.post(
                "/psych/features/extract",
                headers=auth_headers,
                json={
                    "feature_type": "statistical",
                    "dataset_id": 1,
                    "feature_set_name": "基线特征",
                },
            ),
            status_code=201,
        )
    assert data["task_id"] == "task_feat_1"
    assert mock_ext.call_args[0][1] == "statistical"


def test_features_extract_missing_type(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/features/extract",
        headers=auth_headers,
        json={"dataset_id": 1},
    )
    assert_validation_error(r)


def test_features_extract_error(psych_client, auth_headers):
    with patch(
        "backend.psych_feature_service.extract_features",
        return_value=(None, "数据集不存在"),
    ):
        r = psych_client.post(
            "/psych/features/extract",
            headers=auth_headers,
            json={"feature_type": "statistical", "dataset_id": 99},
        )
    assert r.status_code == 400


def test_features_extract_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post(
            "/psych/features/extract",
            json={"feature_type": "statistical"},
        )
    )


def test_list_features_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_feature_service.list_features",
        return_value=([SAMPLE_FEAT], None),
    ) as mock_list:
        data = assert_success(
            psych_client.get("/psych/features?dataset_id=1", headers=auth_headers)
        )
    assert data["features"][0]["id"] == 3
    assert mock_list.call_args[1]["dataset_id"] == 1


def test_list_features_db_error(psych_client, auth_headers):
    with patch(
        "backend.psych_feature_service.list_features",
        return_value=(None, "db"),
    ):
        r = psych_client.get("/psych/features", headers=auth_headers)
    assert r.status_code == 500


def test_list_features_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/features"))


def test_get_feature_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_feature_service.get_feature",
        return_value=(SAMPLE_FEAT, None),
    ):
        data = assert_success(
            psych_client.get("/psych/features/3", headers=auth_headers)
        )
    assert data["feature_set_name"] == "基线特征"


def test_get_feature_not_found(psych_client, auth_headers):
    with patch(
        "backend.psych_feature_service.get_feature",
        return_value=(None, "特征集不存在"),
    ):
        r = psych_client.get("/psych/features/99", headers=auth_headers)
    assert r.status_code == 404


def test_get_feature_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/features/3"))


def test_download_feature_ok(psych_client, auth_headers, tmp_path):
    fpath = tmp_path / "feat.csv"
    fpath.write_text("feature,value\nage__mean,42.0\n", encoding="utf-8")
    with patch(
        "backend.psych_feature_service.download_feature",
        return_value=(
            {
                "file_path": str(fpath),
                "filename": "基线特征_stat.csv",
                "media_type": "text/csv",
            },
            None,
        ),
    ):
        r = psych_client.get("/psych/features/3/download", headers=auth_headers)
    assert r.status_code == 200
    assert "age__mean" in r.text or b"age__mean" in r.content


def test_download_feature_json_ok(psych_client, auth_headers, tmp_path):
    fpath = tmp_path / "feat.json"
    fpath.write_text('[{"feature":"age__mean","value":42.0}]', encoding="utf-8")
    with patch(
        "backend.psych_feature_service.download_feature",
        return_value=(
            {
                "file_path": str(fpath),
                "filename": "基线特征_stat.json",
                "media_type": "application/json",
            },
            None,
        ),
    ) as mock_dl:
        r = psych_client.get(
            "/psych/features/3/download?format=json", headers=auth_headers
        )
    assert r.status_code == 200
    assert mock_dl.call_args.kwargs.get("fmt") == "json"
    assert "age__mean" in r.text or b"age__mean" in r.content


def test_download_feature_not_found(psych_client, auth_headers):
    with patch(
        "backend.psych_feature_service.download_feature",
        return_value=(None, "特征集不存在: 99"),
    ):
        r = psych_client.get("/psych/features/99/download", headers=auth_headers)
    assert r.status_code == 404


def test_download_feature_bad_format(psych_client, auth_headers):
    with patch(
        "backend.psych_feature_service.download_feature",
        return_value=(None, "format 无效，可选: csv, json"),
    ):
        r = psych_client.get(
            "/psych/features/3/download?format=xlsx", headers=auth_headers
        )
    assert r.status_code == 400


def test_download_feature_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/features/3/download"))
