# backend/psych_data_service.py — 多类型数据一体化接入

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from db import psych_store as store
from psych.adapters.solver_runner import load_dataframe
from psych.paths import dataset_storage_path, new_id

logger = logging.getLogger(__name__)

VALID_SOURCE_TYPES = (
    "text",
    "scale",
    "assessment",
    "order",
    "medication",
    "lab",
    "exam",
    "followup",
    "mixed",
    "table",
)


def create_dataset(
    user_id: int,
    name: str,
    source_type: str = "mixed",
    project_id: Optional[int] = None,
    description: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    if not name or not str(name).strip():
        return None, "name 不能为空"
    st = (source_type or "mixed").strip().lower()
    if st not in VALID_SOURCE_TYPES:
        return None, f"source_type 无效，可选: {', '.join(VALID_SOURCE_TYPES)}"
    did, err = store.insert_dataset(
        {
            "user_id": user_id,
            "project_id": project_id,
            "name": str(name).strip(),
            "source_type": st,
            "status": "active",
            "description": description,
            "row_count": 0,
        }
    )
    if err:
        return None, err
    return store.get_dataset(int(did), user_id)  # type: ignore[arg-type]


def list_datasets(user_id: int, limit: int = 50) -> Tuple[List[dict], Optional[str]]:
    return store.list_datasets(user_id, limit=limit)


def get_dataset(dataset_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    ds, err = store.get_dataset(dataset_id, user_id)
    if err:
        return None, err
    if not ds:
        return None, f"数据集不存在: {dataset_id}"
    return ds, None


def _infer_schema(df: pd.DataFrame) -> Dict[str, Any]:
    cols = []
    for c in df.columns:
        s = df[c]
        dtype = "numeric" if pd.api.types.is_numeric_dtype(s) else "categorical"
        if pd.api.types.is_datetime64_any_dtype(s):
            dtype = "datetime"
        elif s.dtype == object and s.dropna().map(lambda x: isinstance(x, str)).mean() > 0.8:
            if s.astype(str).str.len().mean() > 40:
                dtype = "text"
        cols.append(
            {
                "name": str(c),
                "dtype": dtype,
                "n_unique": int(s.nunique(dropna=True)),
                "n_missing": int(s.isna().sum()),
            }
        )
    return {"columns": cols, "n_rows": int(len(df)), "n_cols": int(df.shape[1])}


def ingest_file(
    user_id: int,
    dataset_id: int,
    filename: str,
    content: bytes,
    record_type: str = "row",
    patient_key_col: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    ds, err = get_dataset(dataset_id, user_id)
    if err:
        return None, err

    dest = dataset_storage_path(user_id, filename or f"ds_{dataset_id}.csv")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(content)

    job_id = new_id("ingest_")
    _, jerr = store.insert_ingest_job(
        {
            "job_id": job_id,
            "dataset_id": dataset_id,
            "user_id": user_id,
            "status": "pending",
        }
    )
    if jerr:
        return None, jerr

    from backend.psych_task_service import submit_task

    def _worker(task_id: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        store.update_ingest_job(job_id, {"status": "running"})
        try:
            df = load_dataframe(dest)
            schema = _infer_schema(df)
            # 索引前 N 条记录
            indexed = 0
            pk_col = patient_key_col if patient_key_col and patient_key_col in df.columns else None
            for idx, row in df.head(500).iterrows():
                pk = str(row[pk_col]) if pk_col is not None else None
                store.insert_data_record(
                    {
                        "dataset_id": dataset_id,
                        "record_type": record_type,
                        "patient_key": pk,
                        "payload_path": dest,
                        "tags_json": {"row_index": int(idx) if isinstance(idx, (int,)) else str(idx)},
                    }
                )
                indexed += 1
            store.update_dataset(
                dataset_id,
                {
                    "file_path": dest,
                    "schema_json": schema,
                    "row_count": int(len(df)),
                    "status": "ready",
                },
            )
            stats = {"row_count": int(len(df)), "indexed_records": indexed, "schema": schema}
            store.update_ingest_job(
                job_id,
                {
                    "status": "success",
                    "stats_json": stats,
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            return {"job_id": job_id, "dataset_id": dataset_id, "stats": stats}, None
        except Exception as exc:
            logger.exception("ingest failed")
            store.update_ingest_job(
                job_id,
                {
                    "status": "failed",
                    "error_message": str(exc),
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            return {}, str(exc)

    task, terr = submit_task(
        user_id=user_id,
        module="data_ingest",
        method_id="ingest",
        params={"dataset_id": dataset_id, "file_path": dest, "job_id": job_id},
        worker=_worker,
    )
    if terr:
        return None, terr
    return {"job_id": job_id, "task": task, "file_path": dest}, None


def preview_dataset(
    user_id: int, dataset_id: int, n_rows: int = 20
) -> Tuple[Optional[dict], Optional[str]]:
    ds, err = get_dataset(dataset_id, user_id)
    if err:
        return None, err
    assert ds is not None
    path = ds.get("file_path")
    if not path or not os.path.isfile(path):
        return {"dataset": ds, "preview": [], "message": "无数据文件"}, None
    try:
        df = load_dataframe(path)
        preview = df.head(int(n_rows)).where(pd.notnull(df.head(int(n_rows))), None)
        return {
            "dataset": ds,
            "columns": list(df.columns),
            "preview": preview.to_dict(orient="records"),
            "row_count": int(len(df)),
        }, None
    except Exception as exc:
        return None, f"预览失败: {exc}"


def query_records(
    user_id: int,
    dataset_id: int,
    patient_key: Optional[str] = None,
    record_type: Optional[str] = None,
    limit: int = 100,
) -> Tuple[Optional[dict], Optional[str]]:
    _, err = get_dataset(dataset_id, user_id)
    if err:
        return None, err
    rows, rerr = store.list_data_records(
        dataset_id, patient_key=patient_key, record_type=record_type, limit=limit
    )
    if rerr:
        return None, rerr
    return {"dataset_id": dataset_id, "records": rows, "count": len(rows)}, None
