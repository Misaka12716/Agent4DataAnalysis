"""2.1.4 Psych 功能集成测共享工具（真 MySQL / 真 LLM，不 mock service）。"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.jwt_auth import create_access_token
from backend.psych_routes import register_psych_routes

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
CORRELATION_CSV = FIXTURES / "correlation_clinical_sample.csv"
RISK_CSV = FIXTURES / "risk_training_sample.csv"

TASK_POLL_TIMEOUT_S = 120.0
TASK_POLL_INTERVAL_S = 0.4


def extract_task_id(payload: Any) -> Optional[str]:
    """从 ingest / submit / complete 响应中解析 task_id。"""
    if not isinstance(payload, dict):
        return None
    if payload.get("task_id"):
        return str(payload["task_id"])
    task = payload.get("task")
    if isinstance(task, dict) and task.get("task_id"):
        return str(task["task_id"])
    nested = payload.get("data")
    if isinstance(nested, dict):
        return extract_task_id(nested)
    return None


def make_psych_app() -> FastAPI:
    app = FastAPI()
    register_psych_routes(app)
    return app


def make_psych_and_chunked_app() -> FastAPI:
    from backend.chunked_upload_routes import register_chunked_upload_routes

    app = FastAPI()
    register_psych_routes(app)
    register_chunked_upload_routes(app)
    return app


def auth_headers(user_id: int, username: str, phone: str) -> Dict[str, str]:
    token, _ = create_access_token(user_id, username, phone)
    return {"Authorization": f"Bearer {token}"}


def assert_success(resp, *, status_code: int = 200) -> Any:
    assert resp.status_code == status_code, resp.text
    body = resp.json()
    assert body.get("status") == "success", body
    assert "data" in body
    return body["data"]


def wait_task(
    client: TestClient,
    headers: Dict[str, str],
    task_id: str,
    *,
    timeout_s: float = TASK_POLL_TIMEOUT_S,
    accept: Tuple[str, ...] = ("success", "failed", "cancelled"),
) -> Dict[str, Any]:
    """轮询 GET /psych/tasks/{task_id} 直至终态。"""
    deadline = time.time() + timeout_s
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        r = client.get(f"/psych/tasks/{task_id}", headers=headers)
        assert r.status_code == 200, r.text
        last = r.json()["data"]
        status = str(last.get("status") or "")
        if status in accept:
            return last
        time.sleep(TASK_POLL_INTERVAL_S)
    raise AssertionError(
        f"任务 {task_id} 在 {timeout_s}s 内未进入 {accept}，最后状态={last}"
    )


def wait_task_success(
    client: TestClient,
    headers: Dict[str, str],
    task_id: str,
    *,
    timeout_s: float = TASK_POLL_TIMEOUT_S,
) -> Dict[str, Any]:
    row = wait_task(client, headers, task_id, timeout_s=timeout_s)
    assert row.get("status") == "success", (
        f"任务失败: status={row.get('status')} err={row.get('error_message')} row={row}"
    )
    return row


def write_mini_csv(path: Path) -> Path:
    """带数值与分组列的小表，供统计/ML。"""
    path.write_text(
        "patient_id,age,HAMD_total,HAMA_total,PHQ9_total,group_label,relapse\n"
        "P1,30,20,15,12,A,0\n"
        "P2,40,25,18,16,A,1\n"
        "P3,35,18,14,10,B,0\n"
        "P4,50,30,22,20,B,1\n"
        "P5,28,15,12,8,A,0\n"
        "P6,45,28,20,18,B,1\n"
        "P7,33,22,16,14,A,0\n"
        "P8,55,32,24,21,B,1\n",
        encoding="utf-8",
    )
    return path


def cleanup_psych_user(user_id: int) -> None:
    """按 user_id 清理本套件写入的 psych_* 行（尽量完整、忽略缺失表错误）。"""
    from utils.mysql_utils import mysql_handler
    from db.psych_schema import (
        TABLE_PSYCH_ANALYSIS_PARAMS,
        TABLE_PSYCH_DATA_RECORDS,
        TABLE_PSYCH_DATASETS,
        TABLE_PSYCH_EXPORTS,
        TABLE_PSYCH_FEATURES,
        TABLE_PSYCH_INGEST_JOBS,
        TABLE_PSYCH_LLM_EXTRACTIONS,
        TABLE_PSYCH_ML_MODELS,
        TABLE_PSYCH_PARAM_TEMPLATES,
        TABLE_PSYCH_PIPELINES,
        TABLE_PSYCH_SCALE_SCORES,
        TABLE_PSYCH_STATS_RESULTS,
        TABLE_PSYCH_TASKS,
        TABLE_PSYCH_VAR_CATEGORIES,
        TABLE_PSYCH_VARIABLES,
    )

    # 先删依赖 dataset 的子表
    ds_rows, _ = mysql_handler.query(
        f"SELECT id FROM {TABLE_PSYCH_DATASETS} WHERE user_id=%s", (user_id,)
    )
    ds_ids = [int(r["id"]) for r in (ds_rows or [])]
    for did in ds_ids:
        mysql_handler.execute(
            f"DELETE FROM {TABLE_PSYCH_DATA_RECORDS} WHERE dataset_id=%s", (did,)
        )
        mysql_handler.execute(
            f"DELETE FROM {TABLE_PSYCH_INGEST_JOBS} WHERE dataset_id=%s", (did,)
        )

    task_rows, _ = mysql_handler.query(
        f"SELECT task_id FROM {TABLE_PSYCH_TASKS} WHERE user_id=%s", (user_id,)
    )
    for tr in task_rows or []:
        tid = tr["task_id"]
        mysql_handler.execute(
            f"DELETE FROM {TABLE_PSYCH_STATS_RESULTS} WHERE task_id=%s", (tid,)
        )

    for table in (
        TABLE_PSYCH_VARIABLES,
        TABLE_PSYCH_VAR_CATEGORIES,
        TABLE_PSYCH_PARAM_TEMPLATES,
        TABLE_PSYCH_ANALYSIS_PARAMS,
        TABLE_PSYCH_TASKS,
        TABLE_PSYCH_ML_MODELS,
        TABLE_PSYCH_FEATURES,
        TABLE_PSYCH_SCALE_SCORES,
        TABLE_PSYCH_LLM_EXTRACTIONS,
        TABLE_PSYCH_EXPORTS,
        TABLE_PSYCH_PIPELINES,
        TABLE_PSYCH_DATASETS,
    ):
        mysql_handler.execute(f"DELETE FROM {table} WHERE user_id=%s", (user_id,))
