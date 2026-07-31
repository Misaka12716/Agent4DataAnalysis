# backend/psych_config_export_service.py — 参数调整与结果导出

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from db import psych_store as store
from psych.adapters.solver_runner import load_dataframe
from psych.paths import export_storage_path, new_id

logger = logging.getLogger(__name__)

VALID_SCOPES = ("qc", "stats", "text", "ml", "dl", "general")
VALID_FORMATS = ("csv", "parquet", "json", "rds_compat")


def list_params(user_id: int, scope: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
    return store.list_analysis_params(user_id, scope=scope)


def upsert_params(
    user_id: int, scope: str, items: Dict[str, Any]
) -> Tuple[Optional[dict], Optional[str]]:
    if scope not in VALID_SCOPES:
        return None, f"scope 无效，可选: {', '.join(VALID_SCOPES)}"
    if not items or not isinstance(items, dict):
        return None, "items 必须为非空对象 {key: value}"
    for key, value in items.items():
        err = store.upsert_analysis_param(user_id, scope, str(key), value)
        if err:
            return None, err
    rows, rerr = store.list_analysis_params(user_id, scope=scope)
    if rerr:
        return None, rerr
    return {"scope": scope, "params": rows}, None


def create_export(
    user_id: int,
    kind: str,
    fmt: str = "csv",
    task_id: Optional[str] = None,
    dataset_id: Optional[int] = None,
    data: Optional[Any] = None,
    note: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    fmt = (fmt or "csv").lower()
    if fmt not in VALID_FORMATS:
        return None, f"format 无效，可选: {', '.join(VALID_FORMATS)}"

    export_id = new_id("exp_")
    ext = {"csv": "csv", "parquet": "parquet", "json": "json", "rds_compat": "csv"}[fmt]
    dest = export_storage_path(user_id, export_id, ext)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    df: Optional[pd.DataFrame] = None
    payload_obj: Any = None

    if data is not None:
        if isinstance(data, list):
            df = pd.DataFrame(data)
        elif isinstance(data, dict):
            payload_obj = data
        else:
            return None, "data 仅支持 list[dict] 或 dict"
    elif dataset_id is not None:
        ds, err = store.get_dataset(int(dataset_id), user_id)
        if err:
            return None, err
        if not ds or not ds.get("file_path"):
            return None, "数据集无文件"
        try:
            df = load_dataframe(ds["file_path"])
        except Exception as exc:
            return None, f"加载数据集失败: {exc}"
    elif task_id:
        task, err = store.get_task_by_task_id(task_id)
        if err:
            return None, err
        if not task or int(task.get("user_id") or 0) != int(user_id):
            return None, "任务不存在"
        payload_obj = task.get("result_json") or {}
        # 若有 stats 结果表
        stats_rows, _ = store.list_stats_results(task_id)
        if stats_rows:
            payload_obj = {"task": task, "stats_results": stats_rows}

    try:
        if fmt == "json" or (payload_obj is not None and df is None):
            dest = export_storage_path(user_id, export_id, "json")
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(payload_obj if payload_obj is not None else {}, f, ensure_ascii=False, default=str, indent=2)
            actual_fmt = "json"
        elif fmt == "parquet" and df is not None:
            df.to_parquet(dest, index=False)
            actual_fmt = "parquet"
        else:
            if df is None:
                # rds_compat / csv fallback from payload
                if isinstance(payload_obj, dict):
                    with open(dest, "w", encoding="utf-8") as f:
                        # write a simple key-value friendly csv if possible
                        flat = payload_obj if not isinstance(payload_obj.get("stats_results"), list) else None
                        if flat and all(not isinstance(v, (dict, list)) for v in flat.values()):
                            pd.DataFrame([flat]).to_csv(dest, index=False)
                        else:
                            dest = export_storage_path(user_id, export_id, "json")
                            json.dump(payload_obj, open(dest, "w", encoding="utf-8"), ensure_ascii=False, default=str, indent=2)
                            actual_fmt = "json"
                            # write rds manifest alongside
                            if fmt == "rds_compat":
                                manifest = dest.replace(".json", "_rds_manifest.json")
                                with open(manifest, "w", encoding="utf-8") as mf:
                                    json.dump(
                                        {
                                            "format": "rds_compat",
                                            "note": "用 R 读取: jsonlite::fromJSON 或 data.table::fread(csv)",
                                            "primary_file": dest,
                                        },
                                        mf,
                                        ensure_ascii=False,
                                        indent=2,
                                    )
                            _, eerr = store.insert_export(
                                {
                                    "export_id": export_id,
                                    "user_id": user_id,
                                    "task_id": task_id,
                                    "kind": kind,
                                    "format": actual_fmt if "actual_fmt" in dir() else fmt,
                                    "file_path": dest,
                                    "note": note,
                                }
                            )
                            if eerr:
                                return None, eerr
                            return {
                                "export_id": export_id,
                                "format": actual_fmt if "actual_fmt" in dir() else "json",
                                "file_path": dest,
                                "kind": kind,
                            }, None
                else:
                    return None, "无可导出数据"
            else:
                df.to_csv(dest, index=False)
            actual_fmt = "csv" if fmt != "rds_compat" else "rds_compat"
            if fmt == "rds_compat":
                manifest = dest.replace(".csv", "_rds_manifest.json")
                with open(manifest, "w", encoding="utf-8") as mf:
                    json.dump(
                        {
                            "format": "rds_compat",
                            "note": "R: data.table::fread() 或 readr::read_csv(); Python: pandas.read_csv()",
                            "primary_file": dest,
                            "columns": list(df.columns) if df is not None else [],
                        },
                        mf,
                        ensure_ascii=False,
                        indent=2,
                    )
    except Exception as exc:
        logger.exception("export failed")
        return None, f"导出失败: {exc}"

    _, eerr = store.insert_export(
        {
            "export_id": export_id,
            "user_id": user_id,
            "task_id": task_id,
            "kind": kind,
            "format": actual_fmt,
            "file_path": dest,
            "note": note,
        }
    )
    if eerr:
        return None, eerr
    return {
        "export_id": export_id,
        "format": actual_fmt,
        "file_path": dest,
        "kind": kind,
    }, None


def get_export_file(export_id: str, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    row, err = store.get_export(export_id, user_id)
    if err:
        return None, err
    if not row:
        return None, f"导出不存在: {export_id}"
    path = row.get("file_path")
    if not path or not os.path.isfile(path):
        return None, "导出文件不存在"
    return row, None
