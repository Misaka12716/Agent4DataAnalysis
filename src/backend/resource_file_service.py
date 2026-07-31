# backend/resource_file_service.py
# 个人文件空间：文件夹 / 上传 / 移动 / 删除 / 分类

from __future__ import annotations

import hashlib
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from backend.resource_classify import classify_resource_file, is_resource_upload_allowed, is_table_file
from backend.resource_paths import (
    allocate_file_storage_path,
    assert_under_user_root,
    safe_filename,
)
from configs.config import RESOURCES_MAX_UPLOAD_MB
from db import resource_store as store
from utils.upload_naming import allocate_unique_name, original_basename


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _validate_parent(user_id: int, parent_id: Optional[int]) -> Optional[str]:
    if parent_id is None:
        return None
    node, err = store.get_file_node(user_id, parent_id)
    if err:
        return err
    if not node:
        return "父文件夹不存在"
    if node.get("node_type") != "folder":
        return "parent_id 必须指向文件夹"
    return None


def list_tree(user_id: int, parent_id: Optional[int] = None) -> Tuple[Optional[dict], Optional[str]]:
    verr = _validate_parent(user_id, parent_id)
    if verr:
        return None, verr
    children, err = store.list_children(user_id, parent_id)
    if err:
        return None, err
    return {
        "parent_id": parent_id,
        "items": children,
    }, None


def mkdir(user_id: int, name: str, parent_id: Optional[int] = None) -> Tuple[Optional[dict], Optional[str]]:
    name = (name or "").strip()
    if not name:
        return None, "文件夹名称不能为空"
    if "/" in name or "\\" in name:
        return None, "文件夹名称不能包含路径分隔符"
    verr = _validate_parent(user_id, parent_id)
    if verr:
        return None, verr
    exist, eerr = store.find_sibling_by_name(user_id, parent_id, name)
    if eerr:
        return None, eerr
    if exist:
        return None, f"同级已存在同名节点: {name}"

    node_id, ierr = store.insert_file_node(
        {
            "user_id": user_id,
            "parent_id": parent_id,
            "name": name,
            "node_type": "folder",
            "category": "other",
            "size_bytes": 0,
        }
    )
    if ierr:
        return None, ierr
    return store.get_file_node(user_id, int(node_id))


def upload_file(
    user_id: int,
    filename: str,
    content: bytes,
    parent_id: Optional[int] = None,
    mime: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    client_original = original_basename(filename)
    # 先用清洗名做类型校验
    probe = safe_filename(client_original)
    if not is_resource_upload_allowed(probe):
        return None, f"不允许上传的文件类型: {probe}"

    max_bytes = RESOURCES_MAX_UPLOAD_MB * 1024 * 1024
    if len(content) > max_bytes:
        return None, f"文件超过大小限制 {RESOURCES_MAX_UPLOAD_MB}MB"

    verr = _validate_parent(user_id, parent_id)
    if verr:
        return None, verr

    children, cerr = store.list_children(user_id, parent_id)
    if cerr:
        return None, cerr
    sibling_names = {str(c.get("name") or "") for c in children}

    storage_path, allocated = allocate_file_storage_path(
        user_id, client_original, extra_existing=sibling_names
    )
    stored_name = allocated.stored_name

    category, guessed_mime = classify_resource_file(stored_name)
    try:
        assert_under_user_root(user_id, storage_path)
        with open(storage_path, "wb") as f:
            f.write(content)
    except Exception as exc:
        return None, f"写入文件失败: {exc}"

    checksum = _sha256_file(storage_path)
    node_id, ierr = store.insert_file_node(
        {
            "user_id": user_id,
            "parent_id": parent_id,
            "name": stored_name,
            "node_type": "file",
            "category": category,
            "mime": mime or guessed_mime,
            "size_bytes": len(content),
            "storage_path": storage_path,
            "checksum": checksum,
            "tags": {
                "auto_classified": True,
                "suggest_dataset": is_table_file(stored_name),
                "original_filename": client_original,
                "renamed": allocated.renamed,
            },
        }
    )
    if ierr:
        try:
            os.remove(storage_path)
        except OSError:
            pass
        return None, ierr

    node, nerr = store.get_file_node(user_id, int(node_id))
    if nerr:
        return None, nerr
    return node, None


def move_node(
    user_id: int,
    node_id: int,
    target_parent_id: Optional[int],
) -> Tuple[Optional[dict], Optional[str]]:
    node, err = store.get_file_node(user_id, node_id)
    if err:
        return None, err
    if not node:
        return None, "节点不存在"

    verr = _validate_parent(user_id, target_parent_id)
    if verr:
        return None, verr

    if target_parent_id is not None:
        # 禁止移入自身或其子孙
        descendants, derr = store.list_descendant_ids(user_id, node_id)
        if derr:
            return None, derr
        if target_parent_id in descendants:
            return None, "不能将文件夹移动到自身或其子目录下"

    children, cerr = store.list_children(user_id, target_parent_id)
    if cerr:
        return None, cerr
    sibling_names = {
        str(c.get("name") or "")
        for c in children
        if int(c.get("id") or 0) != int(node_id)
    }

    current_name = str(node.get("name") or "")
    fields: Dict[str, Any] = {"parent_id": target_parent_id}

    if current_name in sibling_names:
        allocated = allocate_unique_name(sibling_names, current_name)
        new_name = allocated.stored_name
        fields["name"] = new_name
        # 文件节点：保持 name == basename(storage_path)
        if node.get("node_type") == "file":
            old_path = node.get("storage_path") or ""
            if old_path and os.path.isfile(old_path):
                try:
                    assert_under_user_root(user_id, old_path)
                    new_path = os.path.join(os.path.dirname(old_path), new_name)
                    # 若目标路径已被占用，再并入磁盘名重算
                    if os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(old_path):
                        disk_names = set(sibling_names)
                        try:
                            disk_names.update(os.listdir(os.path.dirname(old_path)))
                        except OSError:
                            pass
                        allocated = allocate_unique_name(disk_names, current_name)
                        new_name = allocated.stored_name
                        fields["name"] = new_name
                        new_path = os.path.join(os.path.dirname(old_path), new_name)
                    if os.path.abspath(new_path) != os.path.abspath(old_path):
                        os.rename(old_path, new_path)
                        fields["storage_path"] = os.path.abspath(new_path)
                except Exception as exc:
                    return None, f"移动时重命名磁盘文件失败: {exc}"

    uerr = store.update_file_node(user_id, node_id, fields)
    if uerr:
        return None, uerr
    return store.get_file_node(user_id, node_id)


def delete_node(user_id: int, node_id: int) -> Tuple[Optional[dict], Optional[str]]:
    node, err = store.get_file_node(user_id, node_id)
    if err:
        return None, err
    if not node:
        return None, "节点不存在"

    # 若作为活跃数据集来源，给出提示但仍允许软删
    refs = 0
    if node.get("node_type") == "file":
        refs, rerr = store.count_datasets_by_source_file(user_id, node_id)
        if rerr:
            return None, rerr

    derr = store.soft_delete_file_subtree(user_id, node_id)
    if derr:
        return None, derr
    return {
        "id": node_id,
        "deleted": True,
        "referenced_datasets": refs,
        "warning": f"该文件仍被 {refs} 个活跃数据集引用" if refs else None,
    }, None


def get_downloadable(user_id: int, node_id: int) -> Tuple[Optional[dict], Optional[str]]:
    node, err = store.get_file_node(user_id, node_id)
    if err:
        return None, err
    if not node:
        return None, "节点不存在"
    if node.get("node_type") != "file":
        return None, "只能下载文件节点"
    path = node.get("storage_path") or ""
    try:
        assert_under_user_root(user_id, path)
    except ValueError as exc:
        return None, str(exc)
    if not os.path.isfile(path):
        return None, "磁盘文件缺失"
    return node, None


def copy_file_to_path(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
