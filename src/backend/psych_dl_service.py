# backend/psych_dl_service.py — 深度学习训练/推理

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from psych.dl.models import infer_text_model, list_dl_models, train_text_model
from psych.paths import new_id, user_psych_dir

logger = logging.getLogger(__name__)


def get_models() -> List[Dict[str, Any]]:
    return list_dl_models()


def train(
    user_id: int,
    model_id: str,
    texts: List[str],
    labels: List[int],
    epochs: int = 3,
) -> Tuple[Optional[dict], Optional[str]]:
    if model_id not in {m["model_id"] for m in list_dl_models()}:
        return None, f"未知模型: {model_id}"
    if not texts or not labels or len(texts) != len(labels):
        return None, "texts 与 labels 须等长非空"

    from backend.psych_task_service import submit_task

    def _worker(task_id: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        out_dir = f"{params['_artifact_dir']}/dl"
        result, err = train_text_model(
            model_id, texts, labels, out_dir, epochs=int(params.get("epochs") or epochs)
        )
        if err:
            return {}, err
        return {"model_id": model_id, **result}, None

    return submit_task(
        user_id=user_id,
        module="dl",
        method_id=model_id,
        params={"model_id": model_id, "n_samples": len(texts), "epochs": epochs},
        worker=_worker,
    )


def infer(
    user_id: int,
    meta_path: str,
    texts: List[str],
) -> Tuple[Optional[dict], Optional[str]]:
    # 安全：meta_path 须在用户 psych 目录下
    root = user_psych_dir(user_id)
    import os

    abs_meta = os.path.abspath(meta_path)
    if not abs_meta.startswith(os.path.abspath(root)):
        return None, "meta_path 不在用户目录内"
    result, err = infer_text_model(abs_meta, texts)
    if err:
        return None, err
    return result, None
