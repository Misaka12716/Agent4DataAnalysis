# backend/psych_feature_service.py — 特征挖掘

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from db import psych_store as store
from psych.adapters.solver_runner import load_dataframe, run_solver
from psych.paths import feature_storage_path, new_id

logger = logging.getLogger(__name__)

FEATURE_TYPES = {
    "stat": {"solver_id": None, "name_zh": "统计特征"},
    "ts": {"solver_id": "time_series_features", "name_zh": "时序特征"},
    "text": {"solver_id": "text_features", "name_zh": "文本语义特征"},
}


def _stat_features(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            rows.append(
                {
                    "feature": f"{c}__mean",
                    "value": float(s.mean()) if s.notna().any() else None,
                }
            )
            rows.append({"feature": f"{c}__std", "value": float(s.std()) if s.notna().any() else None})
            rows.append({"feature": f"{c}__min", "value": float(s.min()) if s.notna().any() else None})
            rows.append({"feature": f"{c}__max", "value": float(s.max()) if s.notna().any() else None})
            rows.append({"feature": f"{c}__missing_rate", "value": float(s.isna().mean())})
        else:
            rows.append({"feature": f"{c}__n_unique", "value": float(s.nunique(dropna=True))})
            rows.append({"feature": f"{c}__missing_rate", "value": float(s.isna().mean())})
    return pd.DataFrame(rows)


def extract_features(
    user_id: int,
    feature_type: str,
    dataset_id: Optional[int] = None,
    file_path: Optional[str] = None,
    feature_set_name: Optional[str] = None,
    mapping: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    if feature_type not in FEATURE_TYPES:
        return None, f"feature_type 无效，可选: {', '.join(FEATURE_TYPES)}"

    path = file_path
    if not path and dataset_id is not None:
        ds, err = store.get_dataset(int(dataset_id), user_id)
        if err:
            return None, err
        if not ds or not ds.get("file_path"):
            return None, "数据集无文件"
        path = ds["file_path"]
    if not path:
        return None, "需提供 dataset_id 或 file_path"

    name = feature_set_name or f"{feature_type}_{new_id()[:8]}"
    from backend.psych_task_service import submit_task

    def _worker(task_id: str, wparams: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        artifact = wparams["_artifact_dir"]
        try:
            df = load_dataframe(path)
        except Exception as exc:
            return {}, f"加载数据失败: {exc}"

        meta: Dict[str, Any] = {"feature_type": feature_type}
        out_csv = feature_storage_path(user_id, f"{name}.csv")

        if feature_type == "stat":
            feat_df = _stat_features(df)
            feat_df.to_csv(out_csv, index=False)
            meta["n_features"] = int(len(feat_df))
        else:
            solver_id = FEATURE_TYPES[feature_type]["solver_id"]
            res, err = run_solver(
                str(solver_id),
                df,
                artifact,
                mapping_override=mapping,
                solver_params=params,
            )
            if err and res.get("status") != "ok":
                return res, err
            # 找 csv 输出
            chosen = None
            for v in (res.get("outputs") or {}).values():
                if isinstance(v, dict) and v.get("path") and str(v["path"]).endswith(".csv"):
                    chosen = v["path"]
                    break
            if chosen and Path(chosen).is_file():
                import shutil

                shutil.copy2(chosen, out_csv)
            else:
                # fallback: dump empty marker
                pd.DataFrame({"note": ["no csv output from solver"]}).to_csv(out_csv, index=False)
            meta["solver_result"] = {"status": res.get("status"), "mapping": res.get("mapping")}

        fid, ierr = store.insert_feature_set(
            {
                "user_id": user_id,
                "dataset_id": dataset_id,
                "feature_set_name": name,
                "feature_type": feature_type,
                "feature_matrix_path": out_csv,
                "meta_json": meta,
            }
        )
        if ierr:
            return {}, ierr
        return {
            "feature_id": fid,
            "feature_set_name": name,
            "feature_type": feature_type,
            "path": out_csv,
            "meta": meta,
        }, None

    return submit_task(
        user_id=user_id,
        module="features",
        method_id=feature_type,
        params={
            "dataset_id": dataset_id,
            "file_path": path,
            "feature_type": feature_type,
            "feature_set_name": name,
            "mapping": mapping,
            "params": params,
        },
        worker=_worker,
    )


def list_features(user_id: int, dataset_id: Optional[int] = None) -> Tuple[List[dict], Optional[str]]:
    return store.list_feature_sets(user_id, dataset_id=dataset_id)


def get_feature(feat_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    row, err = store.get_feature_set(feat_id, user_id)
    if err:
        return None, err
    if not row:
        return None, f"特征集不存在: {feat_id}"
    return row, None
