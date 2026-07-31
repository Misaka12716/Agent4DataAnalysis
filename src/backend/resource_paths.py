# backend/resource_paths.py
# 个人资源磁盘路径与安全校验

from __future__ import annotations

import os
import uuid
from typing import Optional, Tuple

from configs.config import RESOURCES_ROOT
from utils.upload_naming import (
    AllocatedUploadName,
    allocate_unique_name_in_dir,
    safe_filename,
)

FORBIDDEN_PREFIXES = ("../", "..\\")

# 再导出，供既有 import 兼容
__all__ = [
    "resources_root",
    "user_resources_root",
    "user_files_dir",
    "user_datasets_dir",
    "user_models_dir",
    "safe_filename",
    "assert_under_user_root",
    "allocate_file_storage_path",
    "allocate_dataset_version_path",
    "allocate_model_storage_path",
]


def resources_root() -> str:
    root = os.path.abspath(RESOURCES_ROOT)
    os.makedirs(root, exist_ok=True)
    return root


def user_resources_root(user_id: int) -> str:
    path = os.path.join(resources_root(), str(int(user_id)))
    os.makedirs(path, exist_ok=True)
    for sub in ("files", "datasets", "models"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    return os.path.abspath(path)


def user_files_dir(user_id: int) -> str:
    return os.path.join(user_resources_root(user_id), "files")


def user_datasets_dir(user_id: int) -> str:
    return os.path.join(user_resources_root(user_id), "datasets")


def user_models_dir(user_id: int) -> str:
    return os.path.join(user_resources_root(user_id), "models")


def assert_under_user_root(user_id: int, abs_path: str) -> str:
    """确保路径位于用户资源根下，防止路径穿越。"""
    root = user_resources_root(user_id)
    resolved = os.path.abspath(abs_path)
    if not resolved.startswith(root + os.sep) and resolved != root:
        raise ValueError("非法路径：超出用户资源目录")
    for prefix in FORBIDDEN_PREFIXES:
        if prefix in abs_path.replace("\\", "/"):
            raise ValueError("非法路径：包含相对逃逸片段")
    return resolved


def allocate_file_storage_path(
    user_id: int,
    original_name: str,
    *,
    extra_existing: Optional[set] = None,
) -> Tuple[str, AllocatedUploadName]:
    """
    在用户 files/ 目录分配存盘路径：无冲突保留原名，冲突时加 (N)。
    返回 (绝对路径, AllocatedUploadName)；逻辑名与磁盘 basename 一致。
    """
    directory = user_files_dir(user_id)
    os.makedirs(directory, exist_ok=True)
    allocated = allocate_unique_name_in_dir(directory, original_name)
    if extra_existing and allocated.stored_name in extra_existing:
        from utils.upload_naming import allocate_unique_name

        existing = set()
        try:
            existing.update(os.listdir(directory))
        except OSError:
            pass
        existing.update(extra_existing)
        allocated = allocate_unique_name(existing, original_name)
    path = os.path.join(directory, allocated.stored_name)
    return os.path.abspath(path), allocated


def allocate_dataset_version_path(
    user_id: int,
    dataset_id: int,
    version: int,
    original_name: str,
) -> Tuple[str, AllocatedUploadName]:
    """在 datasets/<id>/vN/ 下分配唯一文件名。"""
    directory = os.path.join(user_datasets_dir(user_id), str(int(dataset_id)), f"v{int(version)}")
    os.makedirs(directory, exist_ok=True)
    allocated = allocate_unique_name_in_dir(directory, original_name)
    return os.path.abspath(os.path.join(directory, allocated.stored_name)), allocated


def allocate_model_storage_path(
    user_id: int,
    model_id: Optional[int],
    original_name: str,
) -> Tuple[str, AllocatedUploadName]:
    """在 models/<id|uuid>/ 下分配唯一文件名。"""
    mid = str(int(model_id)) if model_id else uuid.uuid4().hex
    directory = os.path.join(user_models_dir(user_id), mid)
    os.makedirs(directory, exist_ok=True)
    allocated = allocate_unique_name_in_dir(
        directory, original_name, fallback="model.pkl"
    )
    return os.path.abspath(os.path.join(directory, allocated.stored_name)), allocated
