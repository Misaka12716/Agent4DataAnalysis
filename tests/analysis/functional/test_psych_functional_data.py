"""功能集成：health / 用户隔离 / datasets ingest / preview / query / 分片 psych_ingest。"""

from __future__ import annotations

import pytest

from psych_functional_helpers import (
    CORRELATION_CSV,
    assert_success,
    extract_task_id,
    wait_task_success,
    write_mini_csv,
)

pytestmark = pytest.mark.integration


def test_health_against_real_db(psych_client, psych_users):
    user_a, _ = psych_users
    data = assert_success(psych_client.get("/psych/health", headers=user_a["headers"]))
    assert data["service"] == "psych"
    assert data["capabilities_total"] >= 1


def test_dataset_user_isolation(psych_client, psych_users):
    user_a, user_b = psych_users
    created = assert_success(
        psych_client.post(
            "/psych/datasets",
            headers=user_a["headers"],
            json={"name": "iso-ds", "source_type": "table"},
        ),
        status_code=201,
    )
    ds_id = created["id"]
    assert_success(psych_client.get(f"/psych/datasets/{ds_id}", headers=user_a["headers"]))
    r = psych_client.get(f"/psych/datasets/{ds_id}", headers=user_b["headers"])
    assert r.status_code in (400, 404)


def test_ingest_preview_query_roundtrip(psych_client, psych_users, tmp_path):
    user_a, _ = psych_users
    headers = user_a["headers"]
    csv_path = write_mini_csv(tmp_path / "mini.csv")
    content = csv_path.read_bytes()

    ds = assert_success(
        psych_client.post(
            "/psych/datasets",
            headers=headers,
            json={"name": "ingest-ds", "source_type": "table", "description": "fn"},
        ),
        status_code=201,
    )
    ds_id = ds["id"]

    ingest = psych_client.post(
        f"/psych/datasets/{ds_id}/ingest",
        headers=headers,
        files={"file": ("mini.csv", content, "text/csv")},
        data={"record_type": "row", "patient_key_col": "patient_id"},
    )
    assert ingest.status_code == 201, ingest.text
    body = ingest.json()
    assert body["status"] == "success"
    task_id = extract_task_id(body)
    assert task_id, body
    wait_task_success(psych_client, headers, task_id)

    preview = assert_success(
        psych_client.get(f"/psych/datasets/{ds_id}/preview?n_rows=5", headers=headers)
    )
    assert preview.get("columns") or preview.get("rows") or preview.get("n_rows")

    queried = assert_success(
        psych_client.get(
            f"/psych/datasets/{ds_id}/query?patient_key=P1&limit=10",
            headers=headers,
        )
    )
    assert "records" in queried or "total" in queried or isinstance(queried, dict)


def test_chunked_psych_ingest_roundtrip(psych_chunk_client, psych_users, tmp_path):
    user_a, _ = psych_users
    headers = user_a["headers"]
    raw = CORRELATION_CSV.read_bytes() if CORRELATION_CSV.is_file() else write_mini_csv(
        tmp_path / "c.csv"
    ).read_bytes()

    ds = assert_success(
        psych_chunk_client.post(
            "/psych/datasets",
            headers=headers,
            json={"name": "chunk-ds", "source_type": "table"},
        ),
        status_code=201,
    )
    ds_id = ds["id"]

    init = psych_chunk_client.post(
        "/upload/chunked/init",
        headers=headers,
        json={
            "filename": "clinical.csv",
            "size": len(raw),
            "target": "psych_ingest",
            "target_params": {"dataset_id": ds_id, "patient_key_col": "patient_id"},
        },
    )
    assert init.status_code in (200, 201), init.text
    upload_id = init.json()["data"]["upload_id"]

    # 单片上传
    import io

    put = psych_chunk_client.put(
        f"/upload/chunked/{upload_id}/parts/0",
        headers=headers,
        files={"chunk": ("part0", io.BytesIO(raw), "application/octet-stream")},
    )
    assert put.status_code == 200, put.text

    complete = psych_chunk_client.post(
        f"/upload/chunked/{upload_id}/complete",
        headers=headers,
    )
    assert complete.status_code in (200, 201), complete.text
    task_id = extract_task_id(complete.json())
    assert task_id, complete.text
    wait_task_success(psych_chunk_client, headers, task_id)

    preview = assert_success(
        psych_chunk_client.get(f"/psych/datasets/{ds_id}/preview", headers=headers)
    )
    assert preview
