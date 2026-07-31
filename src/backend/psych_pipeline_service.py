# backend/psych_pipeline_service.py — 统计分析管线适配

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from db import psych_store as store
from psych.adapters.solver_runner import load_dataframe, run_solver
from psych.ml.registry import list_algorithms
from psych.stats.catalog import list_stats_methods

logger = logging.getLogger(__name__)


def list_pipeline_methods() -> Dict[str, Any]:
    return {
        "stats": list_stats_methods(),
        "ml": list_algorithms(),
        "feature": [
            {"method_id": "stat_features", "name_zh": "统计特征"},
            {"method_id": "time_series_features", "name_zh": "时序特征", "solver_id": "time_series_features"},
            {"method_id": "text_features", "name_zh": "文本特征", "solver_id": "text_features"},
        ],
    }


def create_pipeline(
    user_id: int, name: str, steps: List[Dict[str, Any]]
) -> Tuple[Optional[dict], Optional[str]]:
    if not name or not str(name).strip():
        return None, "name 不能为空"
    if not steps or not isinstance(steps, list):
        return None, "steps 必须为非空数组"
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or not step.get("method_id"):
            return None, f"steps[{i}] 缺少 method_id"
    pid, err = store.insert_pipeline(
        {
            "user_id": user_id,
            "name": str(name).strip(),
            "steps_json": steps,
            "enabled": 1,
        }
    )
    if err:
        return None, err
    return store.get_pipeline(int(pid), user_id)  # type: ignore[arg-type]


def list_pipelines(user_id: int) -> Tuple[List[dict], Optional[str]]:
    return store.list_pipelines(user_id)


def get_pipeline(pipe_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    row, err = store.get_pipeline(pipe_id, user_id)
    if err:
        return None, err
    if not row:
        return None, f"管线不存在: {pipe_id}"
    return row, None


def save_param_template(
    user_id: int,
    module: str,
    method_id: str,
    name: str,
    params: Dict[str, Any],
    is_default: bool = False,
) -> Tuple[Optional[dict], Optional[str]]:
    if not name:
        return None, "name 不能为空"
    tid, err = store.insert_param_template(
        {
            "user_id": user_id,
            "module": module,
            "method_id": method_id,
            "name": name,
            "params_json": params or {},
            "is_default": 1 if is_default else 0,
        }
    )
    if err:
        return None, err
    rows, _ = store.list_param_templates(user_id, module=module)
    for r in rows or []:
        if r.get("id") == tid:
            return r, None
    return {"id": tid, "name": name}, None


def list_param_templates(user_id: int, module: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
    return store.list_param_templates(user_id, module=module)


def run_pipeline(
    user_id: int,
    pipe_id: int,
    dataset_id: Optional[int] = None,
    file_path: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    pipe, err = get_pipeline(pipe_id, user_id)
    if err:
        return None, err
    assert pipe is not None
    if not pipe.get("enabled"):
        return None, "管线已禁用"

    path = file_path
    if not path and dataset_id is not None:
        ds, derr = store.get_dataset(int(dataset_id), user_id)
        if derr:
            return None, derr
        if not ds or not ds.get("file_path"):
            return None, "数据集无文件"
        path = ds["file_path"]
    if not path:
        return None, "需提供 dataset_id 或 file_path"

    steps = pipe.get("steps_json") or []
    from backend.psych_task_service import submit_task

    def _worker(task_id: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        artifact = params["_artifact_dir"]
        try:
            df = load_dataframe(path)
        except Exception as exc:
            return {}, f"加载数据失败: {exc}"
        step_results = []
        for i, step in enumerate(steps):
            mid = step.get("method_id")
            solver_id = step.get("solver_id") or mid
            mapping = step.get("mapping") or {}
            sparams = step.get("params") or {}
            sub = f"{artifact}/step_{i}_{solver_id}"
            res, rerr = run_solver(solver_id, df, sub, mapping_override=mapping, solver_params=sparams)
            step_results.append({"step": i, "method_id": mid, "result": res, "error": rerr})
            if rerr and step.get("stop_on_error", True):
                return {"steps": step_results}, f"步骤 {i} ({mid}) 失败: {rerr}"
        return {"pipeline_id": pipe_id, "steps": step_results}, None

    return submit_task(
        user_id=user_id,
        module="pipeline",
        method_id=f"pipeline_{pipe_id}",
        params={"pipeline_id": pipe_id, "dataset_id": dataset_id, "file_path": path},
        worker=_worker,
    )
