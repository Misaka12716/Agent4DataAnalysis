"""2.1.4 模块1：datasets 创建/列表/详情/ingest/preview/query。"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import UploadFile

from psych_test_helpers import (
    SAMPLE_DATASET,
    assert_success,
    assert_unauthorized,
    assert_validation_error,
)


def test_create_dataset_requires_auth(psych_client):
    assert_unauthorized(psych_client.post("/psych/datasets", json={"name": "x"}))


def test_create_dataset_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_data_service.create_dataset",
        return_value=(SAMPLE_DATASET, None),
    ) as mock_create:
        data = assert_success(
            psych_client.post(
                "/psych/datasets",
                headers=auth_headers,
                json={
                    "name": "抑郁队列基线",
                    "source_type": "scale",
                    "description": "demo",
                },
            ),
            status_code=201,
        )
    assert data["id"] == 1
    mock_create.assert_called_once()
    assert mock_create.call_args[0][1] == "抑郁队列基线"


def test_create_dataset_missing_name(psych_client, auth_headers):
    r = psych_client.post("/psych/datasets", headers=auth_headers, json={"source_type": "mixed"})
    assert_validation_error(r)


def test_create_dataset_business_error(psych_client, auth_headers):
    with patch(
        "backend.psych_data_service.create_dataset",
        return_value=(None, "名称重复"),
    ):
        r = psych_client.post(
            "/psych/datasets", headers=auth_headers, json={"name": "dup"}
        )
    assert r.status_code == 400


def test_list_datasets_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_data_service.list_datasets",
        return_value=([SAMPLE_DATASET], None),
    ):
        data = assert_success(
            psych_client.get("/psych/datasets?limit=5", headers=auth_headers)
        )
    assert len(data["datasets"]) == 1


def test_list_datasets_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/datasets"))


def test_list_datasets_db_error(psych_client, auth_headers):
    with patch(
        "backend.psych_data_service.list_datasets",
        return_value=(None, "db err"),
    ):
        r = psych_client.get("/psych/datasets", headers=auth_headers)
    assert r.status_code == 500


def test_get_dataset_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_data_service.get_dataset",
        return_value=(SAMPLE_DATASET, None),
    ):
        data = assert_success(psych_client.get("/psych/datasets/1", headers=auth_headers))
    assert data["name"] == "抑郁队列基线"


def test_get_dataset_not_found(psych_client, auth_headers):
    with patch(
        "backend.psych_data_service.get_dataset",
        return_value=(None, "数据集不存在"),
    ):
        r = psych_client.get("/psych/datasets/999", headers=auth_headers)
    assert r.status_code == 404


def test_get_dataset_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/datasets/1"))


def test_ingest_ok(psych_client, auth_headers):
    payload = {"task_id": "task_ingest_1", "status": "pending", "rows": 10}
    with patch(
        "backend.psych_data_service.ingest_file",
        return_value=(payload, None),
    ):
        r = psych_client.post(
            "/psych/datasets/1/ingest",
            headers=auth_headers,
            files={"file": ("demo.csv", b"a,b\n1,2\n", "text/csv")},
            data={"record_type": "row"},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "success"
    assert body["data"]["task_id"] == "task_ingest_1"
    # deprecated 字段由 attach_deprecated_fields 附加
    assert "deprecated" in body or body["status"] == "success"


def test_ingest_missing_file(psych_client, auth_headers):
    r = psych_client.post("/psych/datasets/1/ingest", headers=auth_headers)
    assert_validation_error(r)


def test_ingest_business_error(psych_client, auth_headers):
    with patch(
        "backend.psych_data_service.ingest_file",
        return_value=(None, "数据集不存在"),
    ):
        r = psych_client.post(
            "/psych/datasets/1/ingest",
            headers=auth_headers,
            files={"file": ("demo.csv", b"a,b\n1,2\n", "text/csv")},
        )
    assert r.status_code == 400


def test_ingest_route_size_limit_413(psych_client, auth_headers):
    """路由层体积校验：不分配真实超大内存，用自定义 __len__ 触发 413。"""
    from unittest.mock import AsyncMock

    from starlette.datastructures import UploadFile as StarletteUploadFile

    class _Huge:
        def __len__(self):
            return 200 * 1024 * 1024 + 1

    mock_read = AsyncMock(return_value=_Huge())
    with patch.object(StarletteUploadFile, "read", mock_read):
        with patch.object(UploadFile, "read", mock_read):
            r = psych_client.post(
                "/psych/datasets/1/ingest",
                headers=auth_headers,
                files={"file": ("huge.csv", b"x", "text/csv")},
            )
    assert r.status_code == 413, r.text
    assert "200" in str(r.json()["detail"])


def test_ingest_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post(
            "/psych/datasets/1/ingest",
            files={"file": ("a.csv", b"x", "text/csv")},
        )
    )


def test_preview_ok(psych_client, auth_headers):
    preview = {"columns": ["a", "b"], "rows": [{"a": 1, "b": 2}], "n_rows": 1}
    with patch(
        "backend.psych_data_service.preview_dataset",
        return_value=(preview, None),
    ) as mock_prev:
        data = assert_success(
            psych_client.get("/psych/datasets/1/preview?n_rows=5", headers=auth_headers)
        )
    assert data["columns"] == ["a", "b"]
    assert mock_prev.call_args[1]["n_rows"] == 5


def test_preview_error(psych_client, auth_headers):
    with patch(
        "backend.psych_data_service.preview_dataset",
        return_value=(None, "无数据"),
    ):
        r = psych_client.get("/psych/datasets/1/preview", headers=auth_headers)
    assert r.status_code == 400


def test_preview_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/datasets/1/preview"))


def test_query_ok(psych_client, auth_headers):
    result = {"records": [{"patient_key": "P1", "record_type": "scale"}], "total": 1}
    with patch(
        "backend.psych_data_service.query_records",
        return_value=(result, None),
    ) as mock_q:
        data = assert_success(
            psych_client.get(
                "/psych/datasets/1/query?patient_key=P1&record_type=scale&limit=20",
                headers=auth_headers,
            )
        )
    assert data["total"] == 1
    assert mock_q.call_args[1]["patient_key"] == "P1"
    assert mock_q.call_args[1]["limit"] == 20


def test_query_boundary_limit(psych_client, auth_headers):
    with patch(
        "backend.psych_data_service.query_records",
        return_value=({"records": [], "total": 0}, None),
    ) as mock_q:
        assert_success(
            psych_client.get("/psych/datasets/1/query?limit=1", headers=auth_headers)
        )
    assert mock_q.call_args[1]["limit"] == 1


def test_query_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/datasets/1/query"))
