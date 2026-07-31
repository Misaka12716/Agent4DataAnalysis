# backend/psych_stats_service.py — ▲一键统计分析

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from db import psych_store as store
from psych.adapters.solver_runner import load_dataframe, run_solvers_batch
from psych.stats.catalog import STATS_METHODS, list_stats_methods, resolve_solver_id

logger = logging.getLogger(__name__)


def get_methods() -> List[Dict[str, Any]]:
    return list_stats_methods()


def _resolve_dataset_path(dataset_id: Optional[int], file_path: Optional[str], user_id: int) -> Tuple[Optional[str], Optional[str]]:
    if file_path:
        return file_path, None
    if dataset_id is None:
        return None, "需提供 dataset_id 或 file_path"
    ds, err = store.get_dataset(int(dataset_id), user_id)
    if err:
        return None, err
    if not ds:
        return None, f"数据集不存在: {dataset_id}"
    path = ds.get("file_path")
    if not path:
        return None, "数据集尚未关联数据文件，请先 ingest"
    return path, None


def run_stats(
    user_id: int,
    method_ids: List[str],
    dataset_id: Optional[int] = None,
    file_path: Optional[str] = None,
    mappings: Optional[Dict[str, Dict[str, Any]]] = None,
    params_by_method: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not method_ids:
        return None, "method_ids 不能为空"
    unknown = [m for m in method_ids if m not in STATS_METHODS]
    if unknown:
        return None, f"未知统计方法: {', '.join(unknown)}"

    path, perr = _resolve_dataset_path(dataset_id, file_path, user_id)
    if perr:
        return None, perr

    solver_ids = []
    sid_to_mid = {}
    for mid in method_ids:
        sid = resolve_solver_id(mid)
        solver_ids.append(sid)
        sid_to_mid[sid] = mid

    # remap mappings/params keys from method_id to solver_id if needed
    mappings = mappings or {}
    params_by_method = params_by_method or {}
    solver_mappings = {}
    solver_params = {}
    for mid in method_ids:
        sid = resolve_solver_id(mid)
        if mid in mappings:
            solver_mappings[sid] = mappings[mid]
        elif sid in mappings:
            solver_mappings[sid] = mappings[sid]
        if mid in params_by_method:
            solver_params[sid] = params_by_method[mid]
        elif sid in params_by_method:
            solver_params[sid] = params_by_method[sid]

    from backend.psych_task_service import submit_task

    def _worker(task_id: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        artifact = params["_artifact_dir"]
        try:
            df = load_dataframe(path)  # type: ignore[arg-type]
        except Exception as exc:
            return {}, f"加载数据失败: {exc}"
        batch = run_solvers_batch(
            solver_ids,
            df,
            artifact,
            mappings=solver_mappings,
            params_by_method=solver_params,
        )
        for res in batch.get("results") or []:
            mid = sid_to_mid.get(res.get("solver_id"), res.get("solver_id"))
            store.insert_stats_result(
                {
                    "task_id": task_id,
                    "method_id": mid,
                    "summary_json": {
                        "status": res.get("status"),
                        "mapping": res.get("mapping"),
                        "error": res.get("error"),
                        "profile_summary": res.get("profile_summary"),
                    },
                    "tables_json": res.get("outputs") or {},
                }
            )
        overall_err = None
        if batch.get("fail_count", 0) and not batch.get("ok_count"):
            overall_err = "全部统计方法执行失败"
        return {
            "dataset_id": dataset_id,
            "file_path": path,
            "method_ids": method_ids,
            "batch": batch,
        }, overall_err

    return submit_task(
        user_id=user_id,
        module="stats",
        method_id=",".join(method_ids),
        params={
            "dataset_id": dataset_id,
            "file_path": path,
            "method_ids": method_ids,
            "mappings": mappings,
            "params_by_method": params_by_method,
        },
        worker=_worker,
    )


def get_stats_results(task_id: str, user_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    from backend.psych_task_service import get_task

    task, err = get_task(task_id, user_id)
    if err:
        return None, err
    rows, rerr = store.list_stats_results(task_id)
    if rerr:
        return None, rerr
    return {"task": task, "results": rows}, None
