# backend/psych_ml_service.py — ▲机器学习算法库

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from db import psych_store as store
from psych.adapters.solver_runner import load_dataframe, run_solver
from psych.ml.registry import get_custom_trainer, list_algorithms, resolve_solver_id
from psych.paths import model_storage_path, new_id

logger = logging.getLogger(__name__)


def get_algorithms() -> List[Dict[str, Any]]:
    return list_algorithms()


def _resolve_data(
    user_id: int, dataset_id: Optional[int], file_path: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
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
        return None, "数据集尚未关联数据文件"
    return path, None


def train_model(
    user_id: int,
    algo_id: str,
    dataset_id: Optional[int] = None,
    file_path: Optional[str] = None,
    mapping: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    model_name: Optional[str] = None,
    sync_resource: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        solver_id = resolve_solver_id(algo_id)
    except KeyError as exc:
        return None, str(exc)

    path, perr = _resolve_data(user_id, dataset_id, file_path)
    if perr:
        return None, perr

    from backend.psych_task_service import submit_task

    display_name = model_name or f"{algo_id}_{new_id()[:8]}"

    def _worker(task_id: str, wparams: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        artifact = wparams["_artifact_dir"]
        try:
            df = load_dataframe(path)  # type: ignore[arg-type]
        except Exception as exc:
            return {}, f"加载数据失败: {exc}"

        solver_result = None
        custom = get_custom_trainer(algo_id)
        if custom is not None:
            try:
                out = custom(df=df, mapping=mapping or {}, params=params or {}, output_dir=artifact)
                metrics = out.get("metrics") if isinstance(out, dict) else {}
                model_path = out.get("model_path") if isinstance(out, dict) else None
            except Exception as exc:
                logger.exception("custom trainer failed")
                return {}, str(exc)
        else:
            res, err = run_solver(
                solver_id,
                df,
                artifact,
                mapping_override=mapping,
                solver_params=params,
            )
            solver_result = res
            if err and res.get("status") != "ok":
                return res, err
            metrics = {}
            outputs = res.get("outputs") or {}
            for k, v in outputs.items():
                if isinstance(v, dict) and "content" in v:
                    metrics[k] = v["content"]
            model_path = str(Path(artifact) / "psych_model_meta.pkl")
            try:
                with open(model_path, "wb") as f:
                    pickle.dump(
                        {
                            "algo_id": algo_id,
                            "solver_id": solver_id,
                            "mapping": mapping or res.get("mapping"),
                            "params": params or {},
                            "outputs": {
                                k: (v.get("path") if isinstance(v, dict) else v) for k, v in outputs.items()
                            },
                        },
                        f,
                    )
            except Exception as exc:
                logger.warning("save model meta failed: %s", exc)
                model_path = None

        features = (mapping or {}).get("feature_columns") or (mapping or {}).get("numeric_columns")
        mid, ierr = store.insert_ml_model(
            {
                "user_id": user_id,
                "task_id": task_id,
                "algo_id": algo_id,
                "model_name": display_name,
                "metrics_json": metrics,
                "feature_list_json": features,
                "model_path": model_path,
                "status": "active",
            }
        )
        if ierr:
            return {}, ierr

        resource_model_id = None
        if sync_resource and model_path and Path(model_path).is_file():
            try:
                from backend import model_registry_service as mrs

                reg, rerr = mrs.register_model(
                    user_id,
                    display_name,
                    model_path,
                    metadata={
                        "model_type": algo_id,
                        "task_type": "psych_ml",
                        "features": features,
                        "metrics": metrics,
                        "params": params or {},
                        "framework": "sklearn",
                    },
                    source="psych_ml",
                    source_ref_id=mid,
                )
                if not rerr and reg:
                    resource_model_id = reg.get("id")
            except Exception as exc:
                logger.warning("sync user_models failed: %s", exc)

        return {
            "psych_model_id": mid,
            "algo_id": algo_id,
            "model_name": display_name,
            "metrics": metrics,
            "model_path": model_path,
            "resource_model_id": resource_model_id,
            "solver_result": solver_result,
        }, None

    return submit_task(
        user_id=user_id,
        module="ml",
        method_id=algo_id,
        params={
            "dataset_id": dataset_id,
            "file_path": path,
            "algo_id": algo_id,
            "mapping": mapping,
            "params": params,
            "model_name": display_name,
        },
        worker=_worker,
    )


def predict(
    user_id: int,
    model_id: int,
    dataset_id: Optional[int] = None,
    file_path: Optional[str] = None,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    model, err = store.get_ml_model(model_id, user_id)
    if err:
        return None, err
    if not model:
        return None, f"模型不存在: {model_id}"

    meta_path = model.get("model_path")
    if not meta_path or not Path(meta_path).is_file():
        return None, "模型文件不存在，无法预测"

    try:
        with open(meta_path, "rb") as f:
            meta = pickle.load(f)
    except Exception as exc:
        return None, f"加载模型元数据失败: {exc}"

    # 若有 predictions.csv 则返回说明；否则重新跑 solver 的预测路径受限，
    # 对行数据做简单 sklearn 回退不可用时返回元数据提示。
    if rows:
        try:
            df = pd.DataFrame(rows)
        except Exception as exc:
            return None, f"rows 无效: {exc}"
    else:
        path, perr = _resolve_data(user_id, dataset_id, file_path)
        if perr:
            return None, perr
        try:
            df = load_dataframe(path)  # type: ignore[arg-type]
        except Exception as exc:
            return None, f"加载数据失败: {exc}"

    # 优先复用训练产物中的 predictions 逻辑：对同一 solver 再跑一遍（映射复用）
    from psych.paths import task_artifact_dir
    from backend.psych_task_service import submit_task

    algo_id = model.get("algo_id") or meta.get("algo_id")
    try:
        solver_id = resolve_solver_id(str(algo_id))
    except KeyError:
        solver_id = meta.get("solver_id") or algo_id

    # 同步预测（轻量）：直接跑 solver，不走长任务（小数据）
    out_dir = task_artifact_dir(user_id, new_id("pred_"))
    res, rerr = run_solver(
        str(solver_id),
        df,
        out_dir,
        mapping_override=meta.get("mapping"),
        solver_params=meta.get("params"),
    )
    if rerr and res.get("status") != "ok":
        return {"model_id": model_id, "warning": rerr, "result": res}, rerr
    return {"model_id": model_id, "algo_id": algo_id, "result": res}, None


def list_models(user_id: int) -> Tuple[List[dict], Optional[str]]:
    return store.list_ml_models(user_id)


def get_model(model_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    row, err = store.get_ml_model(model_id, user_id)
    if err:
        return None, err
    if not row:
        return None, f"模型不存在: {model_id}"
    return row, None
