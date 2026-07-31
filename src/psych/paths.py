# psych/paths.py — 精神专科分析产物磁盘路径

from __future__ import annotations

import os
import uuid
from typing import Tuple

from configs.config import TEMP_FOLDER

PSYCH_ROOT = os.getenv("PSYCH_ROOT", os.path.join(TEMP_FOLDER, "psych"))


def psych_root() -> str:
    root = os.path.abspath(PSYCH_ROOT)
    os.makedirs(root, exist_ok=True)
    return root


def user_psych_dir(user_id: int) -> str:
    path = os.path.join(psych_root(), str(int(user_id)))
    for sub in ("datasets", "tasks", "models", "features", "exports", "scales", "dl"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    return os.path.abspath(path)


def task_artifact_dir(user_id: int, task_id: str) -> str:
    path = os.path.join(user_psych_dir(user_id), "tasks", task_id)
    os.makedirs(path, exist_ok=True)
    return path


def dataset_storage_path(user_id: int, filename: str) -> str:
    safe = os.path.basename(filename) or f"data_{uuid.uuid4().hex[:8]}.csv"
    return os.path.join(user_psych_dir(user_id), "datasets", safe)


def model_storage_path(user_id: int, name: str) -> str:
    safe = os.path.basename(name) or f"model_{uuid.uuid4().hex[:8]}.pkl"
    return os.path.join(user_psych_dir(user_id), "models", safe)


def export_storage_path(user_id: int, export_id: str, ext: str) -> str:
    return os.path.join(user_psych_dir(user_id), "exports", f"{export_id}.{ext.lstrip('.')}")


def feature_storage_path(user_id: int, name: str) -> str:
    safe = os.path.basename(name) or f"feat_{uuid.uuid4().hex[:8]}.csv"
    return os.path.join(user_psych_dir(user_id), "features", safe)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}" if prefix else uuid.uuid4().hex
