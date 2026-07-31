# backend/model_registry_service.py
# 个人模型库：登记 / CRUD / sklearn 在线预测
# 供 clinical risk_prediction_service.register_model 同步调用

from __future__ import annotations

import json
import os
import pickle
import shutil
from typing import Any, Dict, List, Optional, Tuple

from backend.resource_paths import allocate_model_storage_path, assert_under_user_root, safe_filename
from configs.config import RESOURCES_PREDICT_MAX_ROWS
from db import resource_store as store


def register_model(
    user_id: int,
    model_name: str,
    file_path: str,
    metadata: Optional[Dict[str, Any]] = None,
    source: str = "manual",
    source_ref_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    将模型登记到个人模型库。

    若 source=clinical_risk 且已存在同 source_ref_id 记录，则更新元数据与路径。
    文件会复制到 resources/<user_id>/models/ 下（若已在该目录则复用）。
    """
    metadata = metadata or {}
    if not model_name:
        return None, "model_name 不能为空"
    if not file_path or not os.path.isfile(file_path):
        return None, f"模型文件不存在: {file_path}"

    if source == "clinical_risk" and source_ref_id is not None:
        existing, eerr = store.find_model_by_source_ref(user_id, source, int(source_ref_id))
        if eerr:
            return None, eerr
        if existing:
            dest = existing.get("file_path") or ""
            try:
                if os.path.abspath(file_path) != os.path.abspath(dest):
                    dest, _ = allocate_model_storage_path(user_id, int(existing["id"]), os.path.basename(file_path))
                    assert_under_user_root(user_id, dest)
                    shutil.copy2(file_path, dest)
            except Exception as exc:
                return None, f"同步模型文件失败: {exc}"
            fields = {
                "model_name": model_name,
                "model_type": metadata.get("model_type"),
                "task_type": metadata.get("task_type"),
                "features": metadata.get("features"),
                "metrics": metadata.get("metrics"),
                "params": metadata.get("params"),
                "file_path": dest or file_path,
                "status": "active",
            }
            uerr = store.update_model(user_id, int(existing["id"]), fields)
            if uerr:
                return None, uerr
            return store.get_model(user_id, int(existing["id"]))

    # 先插入占位再落盘（使用临时 path）
    mid, ierr = store.insert_model(
        {
            "user_id": user_id,
            "model_name": model_name,
            "framework": metadata.get("framework") or "sklearn",
            "model_type": metadata.get("model_type"),
            "task_type": metadata.get("task_type"),
            "features": metadata.get("features"),
            "metrics": metadata.get("metrics"),
            "params": metadata.get("params"),
            "file_path": file_path,  # 临时，稍后更新
            "source": source,
            "source_ref_id": source_ref_id,
            "status": "active",
        }
    )
    if ierr:
        return None, ierr

    try:
        dest, _ = allocate_model_storage_path(user_id, int(mid), os.path.basename(file_path))
        assert_under_user_root(user_id, dest)
        if os.path.abspath(file_path) != os.path.abspath(dest):
            shutil.copy2(file_path, dest)
        else:
            dest = file_path
    except Exception as exc:
        store.update_model(user_id, int(mid), {"status": "deleted"})
        return None, f"保存模型文件失败: {exc}"

    uerr = store.update_model(user_id, int(mid), {"file_path": dest})
    if uerr:
        return None, uerr
    return store.get_model(user_id, int(mid))


def list_models(
    user_id: int,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[Optional[dict], Optional[str]]:
    rows, total, err = store.list_models(user_id, keyword=keyword, limit=limit, offset=offset)
    if err:
        return None, err
    return {"items": rows, "total": total, "limit": limit, "offset": offset}, None


def get_model(user_id: int, model_id: int) -> Tuple[Optional[dict], Optional[str]]:
    row, err = store.get_model(user_id, model_id)
    if err:
        return None, err
    if not row:
        return None, "模型不存在"
    return row, None


def upload_model(
    user_id: int,
    filename: str,
    content: bytes,
    model_name: str,
    model_type: Optional[str] = None,
    task_type: Optional[str] = None,
    features: Optional[List[str]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    safe = safe_filename(filename, fallback="model.pkl")
    ext = os.path.splitext(safe)[1].lower()
    if ext not in (".pkl", ".joblib"):
        return None, "仅支持 .pkl / .joblib 模型文件"

    # 先写临时文件再 register
    from backend.resource_paths import user_models_dir
    import uuid

    staging = os.path.join(user_models_dir(user_id), f"_upload_{uuid.uuid4().hex}_{safe}")
    try:
        with open(staging, "wb") as f:
            f.write(content)
        # 快速校验可加载
        _load_sklearn(staging)
    except Exception as exc:
        try:
            os.remove(staging)
        except OSError:
            pass
        return None, f"模型文件无效或无法加载: {exc}"

    result, err = register_model(
        user_id=user_id,
        model_name=model_name or os.path.splitext(safe)[0],
        file_path=staging,
        metadata={
            "framework": "sklearn",
            "model_type": model_type,
            "task_type": task_type,
            "features": features,
            "metrics": metrics,
            "params": params,
        },
        source="manual",
    )
    try:
        os.remove(staging)
    except OSError:
        pass
    return result, err


def delete_model(user_id: int, model_id: int) -> Tuple[Optional[dict], Optional[str]]:
    row, err = store.get_model(user_id, model_id)
    if err:
        return None, err
    if not row:
        return None, "模型不存在"
    uerr = store.update_model(user_id, model_id, {"status": "deleted"})
    if uerr:
        return None, uerr
    path = row.get("file_path") or ""
    try:
        if path and os.path.isfile(path):
            assert_under_user_root(user_id, path)
            os.remove(path)
    except Exception:
        pass
    return {"id": model_id, "deleted": True}, None


def get_downloadable(user_id: int, model_id: int) -> Tuple[Optional[dict], Optional[str]]:
    row, err = get_model(user_id, model_id)
    if err:
        return None, err
    path = row.get("file_path") or ""
    try:
        assert_under_user_root(user_id, path)
    except ValueError as exc:
        return None, str(exc)
    if not os.path.isfile(path):
        return None, "模型文件缺失"
    return {"path": path, "filename": os.path.basename(path), "model": row}, None


def _load_sklearn(path: str) -> Any:
    try:
        import joblib

        return joblib.load(path)
    except Exception:
        with open(path, "rb") as f:
            return pickle.load(f)


def predict(
    user_id: int,
    model_id: int,
    rows: List[Dict[str, Any]],
) -> Tuple[Optional[dict], Optional[str]]:
    if not rows:
        return None, "rows 不能为空"
    if len(rows) > RESOURCES_PREDICT_MAX_ROWS:
        return None, f"单次预测行数不能超过 {RESOURCES_PREDICT_MAX_ROWS}"

    row, err = get_model(user_id, model_id)
    if err:
        return None, err
    path = row.get("file_path") or ""
    try:
        assert_under_user_root(user_id, path)
        model = _load_sklearn(path)
    except Exception as exc:
        return None, f"加载模型失败: {exc}"

    features = row.get("features")
    if isinstance(features, str):
        try:
            features = json.loads(features)
        except Exception:
            features = None

    try:
        import pandas as pd

        df = pd.DataFrame(rows)
        if features and isinstance(features, list) and len(features) > 0:
            missing = [c for c in features if c not in df.columns]
            if missing:
                return None, f"缺少特征列: {', '.join(missing)}"
            X = df[features]
        else:
            X = df

        preds = model.predict(X)
        proba = None
        if hasattr(model, "predict_proba"):
            try:
                proba = model.predict_proba(X)
                proba = [[float(x) for x in row_p] for row_p in proba]
            except Exception:
                proba = None

        pred_list = []
        for p in preds:
            if hasattr(p, "item"):
                try:
                    p = p.item()
                except Exception:
                    p = str(p)
            pred_list.append(p if isinstance(p, (str, int, float, bool)) else str(p))

        return {
            "model_id": model_id,
            "model_name": row.get("model_name"),
            "n_rows": len(rows),
            "features_used": list(X.columns),
            "predictions": pred_list,
            "probabilities": proba,
        }, None
    except Exception as exc:
        return None, f"预测失败: {exc}"
