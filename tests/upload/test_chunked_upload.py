"""分片上传：服务层单测 + session / resources_file 路由冒烟。"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# 避免 reader 链路拉取 langchain_openai（部分 CI/环境未安装）
sys.modules.setdefault("langchain_openai", MagicMock())
sys.modules.setdefault("reader.agent", MagicMock())

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.chunked_upload_routes import register_chunked_upload_routes
from backend.chunked_upload_service import (
    MIN_CHUNK_SIZE,
    UPLOAD_TTL_SECONDS,
    VALID_TARGETS,
    abort_upload,
    cleanup_expired,
    get_upload_status,
    init_upload,
    merge_parts,
    put_part,
)
from backend.jwt_auth import create_access_token
from runtime.factory import clear_runtime_cache

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LARGE_CSV = FIXTURES / "table" / "large-dataset.csv"
PATIENT_DCM = FIXTURES / "imaging" / "患者CT.dcm"


@pytest.fixture
def chunk_tmp(tmp_path, monkeypatch):
    root = str(tmp_path) + os.sep
    monkeypatch.setattr("backend.chunked_upload_service.TEMP_FOLDER", root)
    monkeypatch.setattr("configs.config.TEMP_FOLDER", root)
    monkeypatch.setenv("TEMP_FOLDER", root)
    return tmp_path


@pytest.fixture
def auth_headers():
    token, _ = create_access_token(10, "tester", "13800138000")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_rbac_user():
    user_row = {
        "id": 10,
        "username": "tester",
        "phone": "13800138000",
        "platform_role": "user",
        "status": "active",
    }
    with patch("db.rbac_store.RbacStore.get_user", return_value=(user_row, None)):
        yield user_row


def test_init_put_merge_happy_path(chunk_tmp):
    payload, err, code = init_upload(
        10,
        filename="demo.csv",
        size=12,
        target="session",
        target_params={"session_id": "sid-1"},
        chunk_size=5,
    )
    # chunk_size clamped to >= 1MB; with size=12 → 1 chunk
    assert err is None and code is None
    assert payload["total_chunks"] == 1
    upload_id = payload["upload_id"]
    cs = payload["chunk_size"]

    # recreate with size matching one small chunk by using exact size == chunk after clamp
    # Use multi-chunk with larger size
    data = b"abcdefghij"  # 10 bytes — still 1 chunk at 1MB min
    part, err, code = put_part(10, upload_id, 0, data)
    # size was 12, so expected part size is 12
    assert err is not None  # size mismatch

    payload2, err, code = init_upload(
        10,
        filename="demo.csv",
        size=10,
        target="resources_file",
        target_params={},
        chunk_size=1024 * 1024,
    )
    assert err is None
    uid = payload2["upload_id"]
    body = b"0123456789"
    part, err, code = put_part(10, uid, 0, body)
    assert err is None
    assert part["received"] is True

    # idempotent re-put
    part2, err, code = put_part(10, uid, 0, body)
    assert err is None

    status, err, code = get_upload_status(10, uid)
    assert err is None
    assert status["missing_parts"] == []
    assert status["uploaded_parts"] == [0]

    merged, meta, err, code = merge_parts(10, uid)
    assert err is None
    assert os.path.isfile(merged)
    with open(merged, "rb") as f:
        assert f.read() == body


def test_missing_parts_reject_complete(chunk_tmp):
    # Force small chunk via monkeypatch clamp
    import backend.chunked_upload_service as svc

    monkey_cs = 4
    original = svc.clamp_chunk_size
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(svc, "clamp_chunk_size", lambda x: monkey_cs if x else monkey_cs)
    monkeypatch.setattr(svc, "MIN_CHUNK_SIZE", 1)
    try:
        payload, err, _ = init_upload(
            1,
            filename="a.bin",
            size=10,
            target="session",
            target_params={"session_id": "s"},
            chunk_size=4,
        )
        assert err is None
        uid = payload["upload_id"]
        assert payload["total_chunks"] == 3  # 4+4+2
        put_part(1, uid, 0, b"1234")
        merged, meta, err, code = merge_parts(1, uid)
        assert merged is None
        assert code == 400
        assert "缺少" in (err or "")
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize("filename", ["a.csv", "a.tsv", "a.xlsx", "a.xls"])
def test_init_upload_allows_table_extensions(chunk_tmp, filename):
    payload, err, code = init_upload(
        2,
        filename=filename,
        size=16,
        target="resources_file",
        target_params={},
    )
    assert err is None and code is None
    assert payload["upload_id"]
    assert payload["filename"] == filename


@pytest.mark.parametrize(
    "filename",
    [
        "sample.txt",
        "sample.md",
        "sample.json",
        "sample.yaml",
        "sample.xml",
        "sample.html",
        "sample.log",
    ],
)
def test_init_upload_allows_text_extensions(chunk_tmp, filename):
    payload, err, code = init_upload(
        2,
        filename=filename,
        size=16,
        target="resources_file",
        target_params={},
    )
    assert err is None and code is None
    assert payload["upload_id"]
    assert payload["filename"] == filename


def test_invalid_target_rejected(chunk_tmp):
    payload, err, code = init_upload(
        1,
        filename="a.csv",
        size=10,
        target="workbench",
        target_params={},
    )
    assert payload is None
    assert code == 400
    assert "workbench" in (err or "") or "不支持" in (err or "")
    assert "workbench" not in VALID_TARGETS


def test_cleanup_expired(chunk_tmp):
    payload, err, _ = init_upload(
        3,
        filename="old.csv",
        size=5,
        target="resources_file",
        target_params={},
    )
    assert err is None
    uid = payload["upload_id"]
    meta_path = (
        chunk_tmp / "chunked_uploads" / "3" / uid / "meta.json"
    )
    import json

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["created_at"] = time.time() - UPLOAD_TTL_SECONDS - 10
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)

    removed = cleanup_expired()
    assert removed >= 1
    assert not meta_path.exists()


def test_abort_upload(chunk_tmp):
    payload, err, _ = init_upload(
        4,
        filename="x.csv",
        size=3,
        target="resources_file",
        target_params={},
    )
    uid = payload["upload_id"]
    out, err, code = abort_upload(4, uid)
    assert err is None
    assert out["aborted"] is True
    _, err, code = get_upload_status(4, uid)
    assert code == 404


def _make_app() -> FastAPI:
    app = FastAPI()
    register_chunked_upload_routes(app)
    return app


@pytest.fixture
def client(chunk_tmp, isolated_workspaces, mock_rbac_user, monkeypatch):
    monkeypatch.setattr("backend.chunked_upload_service.TEMP_FOLDER", str(chunk_tmp) + os.sep)
    yield TestClient(_make_app())


@pytest.fixture
def session_workspace(isolated_workspaces):
    project_root = isolated_workspaces / "10" / "1"
    session_root = project_root / "sessions" / "sid-chunk"
    session_root.mkdir(parents=True, exist_ok=True)
    for sub in ("raw", "processed", "outputs", "archive", "sessions"):
        (project_root / sub).mkdir(exist_ok=True)

    active = {
        "id": 1,
        "user_id": 10,
        "name": "Demo",
        "status": "active",
        "workspace_abs_path": str(project_root),
    }
    session = {
        "session_id": "sid-chunk",
        "user_id": 10,
        "project_id": 1,
        "workspace_abs_path": str(session_root),
    }
    return project_root, session_root, active, session


@pytest.fixture
def patch_session_access(session_workspace):
    _project_root, session_root, active, session = session_workspace
    patches = [
        patch("db.session_store.SessionStore.get_session_user", return_value=(session, None)),
        patch(
            "db.session_store.SessionStore.get_workspace_path",
            return_value=str(session_root),
        ),
        patch("db.project_store.ProjectStore.get_project", return_value=(active, None)),
        patch(
            "backend.chunked_upload_finalize.resolve_project_root",
            return_value=str(_project_root),
        ),
        patch(
            "backend.permission_service.get_effective_project_permissions",
            return_value=(
                {
                    "data_upload",
                    "data_delete",
                    "data_download",
                    "analysis_create",
                },
                "owner",
                None,
            ),
        ),
        patch("backend.route_services._schedule_workspace_snapshot", MagicMock()),
        patch("backend.chunked_upload_finalize.register_upload", MagicMock()),
        patch(
            "backend.chunked_upload_finalize.is_upload_allowed",
            return_value=True,
        ),
        patch(
            "backend.chunked_upload_finalize.classify_file",
            return_value="table",
        ),
    ]
    for p in patches:
        p.start()
    yield session_root
    for p in patches:
        p.stop()
    clear_runtime_cache("sid-chunk")


def test_session_chunked_flow_via_api(client, auth_headers, patch_session_access):
    content = b"col1,col2\n1,2\n3,4\n"
    init = client.post(
        "/upload/chunked/init",
        headers=auth_headers,
        json={
            "filename": "demo.csv",
            "size": len(content),
            "target": "session",
            "target_params": {"session_id": "sid-chunk"},
            "chunk_size": 1024 * 1024,
        },
    )
    assert init.status_code == 201, init.text
    upload_id = init.json()["data"]["upload_id"]

    put = client.put(
        f"/upload/chunked/{upload_id}/parts/0",
        headers=auth_headers,
        files={"chunk": ("part0", io.BytesIO(content), "application/octet-stream")},
    )
    assert put.status_code == 200, put.text

    status = client.get(f"/upload/chunked/{upload_id}", headers=auth_headers)
    assert status.status_code == 200
    assert status.json()["data"]["missing_parts"] == []

    complete = client.post(f"/upload/chunked/{upload_id}/complete", headers=auth_headers)
    assert complete.status_code == 200, complete.text
    body = complete.json()
    assert body["status"] == "success"
    assert body["relative_path"] == "demo.csv"
    assert body["upload_id"] == upload_id
    assert (patch_session_access / "demo.csv").is_file()
    assert (patch_session_access / "demo.csv").read_bytes() == content


def test_resources_file_chunked_smoke(client, auth_headers, monkeypatch):
    content = b"a,b\n1,2\n"
    monkeypatch.setattr(
        "backend.chunked_upload_finalize.is_resource_upload_allowed"
        if False
        else "backend.resource_classify.is_resource_upload_allowed",
        lambda name: True,
    )
    # validate_init imports inside function — patch resource_classify
    with patch(
        "backend.resource_classify.is_resource_upload_allowed", return_value=True
    ), patch(
        "backend.resource_file_service.upload_file",
        return_value=({"id": 99, "name": "t.csv"}, None),
    ):
        init = client.post(
            "/upload/chunked/init",
            headers=auth_headers,
            json={
                "filename": "t.csv",
                "size": len(content),
                "target": "resources_file",
                "target_params": {},
            },
        )
        assert init.status_code == 201, init.text
        upload_id = init.json()["data"]["upload_id"]
        put = client.put(
            f"/upload/chunked/{upload_id}/parts/0",
            headers=auth_headers,
            files={"chunk": ("p0", io.BytesIO(content), "application/octet-stream")},
        )
        assert put.status_code == 200, put.text
        complete = client.post(
            f"/upload/chunked/{upload_id}/complete", headers=auth_headers
        )
        assert complete.status_code == 201, complete.text
        body = complete.json()
        assert body["status"] == "success"
        assert body["data"]["id"] == 99
        assert body["upload_id"] == upload_id


def test_init_rejects_workbench_target(client, auth_headers):
    resp = client.post(
        "/upload/chunked/init",
        headers=auth_headers,
        json={
            "filename": "a.csv",
            "size": 10,
            "target": "workbench",
            "target_params": {"session_id": "wb_x"},
        },
    )
    assert resp.status_code == 400


def test_merge_large_dataset_fixture_multi_chunk(chunk_tmp):
    assert LARGE_CSV.is_file()
    size = LARGE_CSV.stat().st_size
    chunk_size = MIN_CHUNK_SIZE  # 1MB → total_chunks ≥ 2 for ~10MB file
    payload, err, code = init_upload(
        11,
        filename="large-dataset.csv",
        size=size,
        target="resources_file",
        target_params={},
        chunk_size=chunk_size,
    )
    assert err is None and code is None
    assert payload["total_chunks"] >= 2
    uid = payload["upload_id"]
    cs = int(payload["chunk_size"])
    total = int(payload["total_chunks"])

    with LARGE_CSV.open("rb") as f:
        for idx in range(total):
            data = f.read(cs)
            assert data, f"unexpected EOF at part {idx}"
            part, err, code = put_part(11, uid, idx, data)
            assert err is None, err
            assert part["received"] is True

    merged, meta, err, code = merge_parts(11, uid)
    assert err is None, err
    assert merged and os.path.isfile(merged)
    assert os.path.getsize(merged) == size
    hasher = hashlib.sha256()
    with open(merged, "rb") as f:
        while True:
            buf = f.read(1024 * 1024)
            if not buf:
                break
            hasher.update(buf)
    src_hash = hashlib.sha256(LARGE_CSV.read_bytes()).hexdigest()
    assert hasher.hexdigest() == src_hash
    assert meta.get("filename") == "large-dataset.csv"


def test_init_chinese_dicom_filename(chunk_tmp):
    assert PATIENT_DCM.is_file()
    size = PATIENT_DCM.stat().st_size
    payload, err, code = init_upload(
        12,
        filename="患者CT.dcm",
        size=size,
        target="resources_file",
        target_params={},
        chunk_size=MIN_CHUNK_SIZE,
    )
    assert err is None and code is None
    assert payload["filename"] == "患者CT.dcm"
    assert payload["total_chunks"] >= 2
    assert payload["size"] == size
