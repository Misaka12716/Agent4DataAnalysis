# backend/resource_dataset_service.py
# 数据集：元数据解析、版本管理、日常运维

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from backend.resource_paths import allocate_dataset_version_path, assert_under_user_root, safe_filename
from configs.config import RESOURCES_PREVIEW_ROWS
from db import resource_store as store


def parse_tabular_meta(path: str, preview_rows: Optional[int] = None) -> Tuple[Optional[dict], Optional[str]]:
    """解析表格元数据：列名、dtype、缺失统计、预览行。"""
    try:
        import pandas as pd
        import numpy as np
    except Exception as exc:
        return None, f"pandas 不可用: {exc}"

    max_preview = preview_rows if preview_rows is not None else RESOURCES_PREVIEW_ROWS
    ext = os.path.splitext(path)[1].lower()
    name_lower = path.lower()
    try:
        if name_lower.endswith(".nii.gz"):
            return None, "NIfTI 不能作为表格数据集"
        if ext == ".csv":
            df = pd.read_csv(path)
        elif ext == ".tsv":
            df = pd.read_csv(path, sep="\t")
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(path)
        elif ext == ".parquet":
            df = pd.read_parquet(path)
        else:
            return None, f"不支持的数据集格式: {ext}"
    except Exception as exc:
        return None, f"解析数据集失败: {exc}"

    schema = []
    missing = {}
    for col in df.columns:
        series = df[col]
        null_count = int(series.isna().sum())
        schema.append(
            {
                "name": str(col),
                "dtype": str(series.dtype),
                "non_null": int(series.notna().sum()),
                "null_count": null_count,
                "null_rate": round(null_count / max(len(df), 1), 6),
            }
        )
        missing[str(col)] = {
            "null_count": null_count,
            "null_rate": round(null_count / max(len(df), 1), 6),
        }

    preview = []
    for _, row in df.head(max_preview).iterrows():
        rec = {}
        for c in df.columns:
            val = row[c]
            if isinstance(val, float) and (val != val):  # NaN
                val = None
            elif hasattr(val, "item"):
                try:
                    val = val.item()
                except Exception:
                    val = str(val)
            if val is not None and not isinstance(val, (str, int, float, bool)):
                val = str(val)
            rec[str(c)] = val
        preview.append(rec)

    return {
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "schema_json": schema,
        "missing_stats_json": missing,
        "preview_json": {
            "columns": [str(c) for c in df.columns],
            "rows": preview,
        },
    }, None


def _copy_source_to_version(
    user_id: int,
    dataset_id: int,
    version: int,
    src_path: str,
    original_name: str,
) -> Tuple[Optional[str], Optional[str]]:
    dst, _allocated = allocate_dataset_version_path(user_id, dataset_id, version, original_name)
    try:
        assert_under_user_root(user_id, dst)
        shutil.copy2(src_path, dst)
        return dst, None
    except Exception as exc:
        return None, f"复制数据集文件失败: {exc}"


def create_from_file(
    user_id: int,
    source_file_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    node, err = store.get_file_node(user_id, source_file_id)
    if err:
        return None, err
    if not node:
        return None, "来源文件不存在"
    if node.get("node_type") != "file":
        return None, "只能从文件节点创建数据集"
    if node.get("category") != "table":
        return None, "仅支持表格类文件创建数据集"

    src = node.get("storage_path") or ""
    if not os.path.isfile(src):
        return None, "来源文件磁盘缺失"

    meta, merr = parse_tabular_meta(src)
    if merr:
        return None, merr

    ds_name = (name or "").strip() or os.path.splitext(node["name"])[0]
    ds_id, ierr = store.insert_dataset(
        {
            "user_id": user_id,
            "name": ds_name,
            "description": description or "",
            "category": "table",
            "source_file_id": source_file_id,
            "current_version": 1,
            "status": "active",
        }
    )
    if ierr:
        return None, ierr

    storage, cerr = _copy_source_to_version(user_id, int(ds_id), 1, src, node["name"])
    if cerr:
        return None, cerr

    _, verr = store.insert_dataset_version(
        {
            "dataset_id": int(ds_id),
            "version": 1,
            "storage_path": storage,
            "row_count": meta["row_count"],
            "column_count": meta["column_count"],
            "schema_json": meta["schema_json"],
            "missing_stats_json": meta["missing_stats_json"],
            "preview_json": meta["preview_json"],
            "note": "初始版本",
        }
    )
    if verr:
        return None, verr

    return get_detail(user_id, int(ds_id))


def create_from_upload(
    user_id: int,
    filename: str,
    content: bytes,
    name: Optional[str] = None,
    description: Optional[str] = None,
    note: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    from backend.resource_file_service import upload_file

    # 先落入文件空间根目录，再晋升为数据集
    node, uerr = upload_file(user_id, filename, content, parent_id=None)
    if uerr:
        return None, uerr
    return create_from_file(
        user_id,
        int(node["id"]),
        name=name or os.path.splitext(filename)[0],
        description=description,
    )


def list_datasets(
    user_id: int,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[Optional[dict], Optional[str]]:
    rows, total, err = store.list_datasets(user_id, status=status, keyword=keyword, limit=limit, offset=offset)
    if err:
        return None, err
    return {"items": rows, "total": total, "limit": limit, "offset": offset}, None


def get_detail(user_id: int, dataset_id: int) -> Tuple[Optional[dict], Optional[str]]:
    ds, err = store.get_dataset(user_id, dataset_id)
    if err:
        return None, err
    if not ds:
        return None, "数据集不存在"
    ver, verr = store.get_dataset_version(dataset_id, int(ds["current_version"]))
    if verr:
        return None, verr
    return {"dataset": ds, "current_version_meta": ver}, None


def get_preview(user_id: int, dataset_id: int) -> Tuple[Optional[dict], Optional[str]]:
    detail, err = get_detail(user_id, dataset_id)
    if err:
        return None, err
    ver = detail.get("current_version_meta") or {}
    preview = ver.get("preview_json") or {}
    return {
        "dataset_id": dataset_id,
        "version": ver.get("version"),
        "preview": preview,
        "schema": ver.get("schema_json"),
        "missing_stats": ver.get("missing_stats_json"),
        "row_count": ver.get("row_count"),
        "column_count": ver.get("column_count"),
    }, None


def get_downloadable(user_id: int, dataset_id: int) -> Tuple[Optional[dict], Optional[str]]:
    detail, err = get_detail(user_id, dataset_id)
    if err:
        return None, err
    ver = detail.get("current_version_meta") or {}
    path = ver.get("storage_path") or ""
    try:
        assert_under_user_root(user_id, path)
    except ValueError as exc:
        return None, str(exc)
    if not os.path.isfile(path):
        return None, "版本文件缺失"
    return {
        "path": path,
        "filename": os.path.basename(path),
        "dataset": detail["dataset"],
        "version": ver.get("version"),
    }, None


def add_version(
    user_id: int,
    dataset_id: int,
    filename: str,
    content: bytes,
    note: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    ds, err = store.get_dataset(user_id, dataset_id)
    if err:
        return None, err
    if not ds:
        return None, "数据集不存在"
    if ds.get("status") == "archived":
        return None, "已归档数据集不可上传新版本，请先恢复"

    # 写入临时文件再解析
    safe = safe_filename(filename)
    tmp_path, _ = allocate_dataset_version_path(user_id, dataset_id, 0, "tmp")
    # allocate creates v0 dir; use a staging path
    staging_dir = os.path.dirname(tmp_path)
    os.makedirs(staging_dir, exist_ok=True)
    staging = os.path.join(staging_dir, f"_staging_{safe}")
    with open(staging, "wb") as f:
        f.write(content)

    meta, merr = parse_tabular_meta(staging)
    if merr:
        try:
            os.remove(staging)
        except OSError:
            pass
        return None, merr

    new_version = int(ds["current_version"]) + 1
    final_path, _ = allocate_dataset_version_path(user_id, dataset_id, new_version, safe)
    try:
        shutil.move(staging, final_path)
    except Exception as exc:
        return None, f"保存版本文件失败: {exc}"

    _, verr = store.insert_dataset_version(
        {
            "dataset_id": dataset_id,
            "version": new_version,
            "storage_path": final_path,
            "row_count": meta["row_count"],
            "column_count": meta["column_count"],
            "schema_json": meta["schema_json"],
            "missing_stats_json": meta["missing_stats_json"],
            "preview_json": meta["preview_json"],
            "note": note or f"版本 {new_version}",
        }
    )
    if verr:
        return None, verr

    uerr = store.update_dataset(user_id, dataset_id, {"current_version": new_version})
    if uerr:
        return None, uerr
    return get_detail(user_id, dataset_id)


def list_versions(user_id: int, dataset_id: int) -> Tuple[Optional[dict], Optional[str]]:
    ds, err = store.get_dataset(user_id, dataset_id)
    if err:
        return None, err
    if not ds:
        return None, "数据集不存在"
    versions, verr = store.list_dataset_versions(dataset_id)
    if verr:
        return None, verr
    # 列表不返回完整 preview 以减小载荷
    slim = []
    for v in versions:
        item = dict(v)
        item.pop("preview_json", None)
        slim.append(item)
    return {
        "dataset_id": dataset_id,
        "current_version": ds.get("current_version"),
        "versions": slim,
    }, None


def rollback(user_id: int, dataset_id: int, version: int) -> Tuple[Optional[dict], Optional[str]]:
    ds, err = store.get_dataset(user_id, dataset_id)
    if err:
        return None, err
    if not ds:
        return None, "数据集不存在"
    ver, verr = store.get_dataset_version(dataset_id, version)
    if verr:
        return None, verr
    if not ver:
        return None, f"版本不存在: {version}"
    uerr = store.update_dataset(user_id, dataset_id, {"current_version": int(version)})
    if uerr:
        return None, uerr
    return get_detail(user_id, dataset_id)


def update_dataset(
    user_id: int,
    dataset_id: int,
    fields: Dict[str, Any],
) -> Tuple[Optional[dict], Optional[str]]:
    ds, err = store.get_dataset(user_id, dataset_id)
    if err:
        return None, err
    if not ds:
        return None, "数据集不存在"
    allowed = {}
    if "name" in fields and fields["name"] is not None:
        allowed["name"] = str(fields["name"]).strip()
    if "description" in fields and fields["description"] is not None:
        allowed["description"] = fields["description"]
    if "status" in fields and fields["status"] is not None:
        status = str(fields["status"]).strip()
        if status not in ("active", "archived"):
            return None, "status 必须为 active 或 archived"
        allowed["status"] = status
    if not allowed:
        return None, "没有可更新字段"
    uerr = store.update_dataset(user_id, dataset_id, allowed)
    if uerr:
        return None, uerr
    return get_detail(user_id, dataset_id)


def archive_or_delete(user_id: int, dataset_id: int, hard: bool = False) -> Tuple[Optional[dict], Optional[str]]:
    """默认归档；hard=True 时仍只做归档标记（保留版本文件便于恢复）。"""
    return update_dataset(user_id, dataset_id, {"status": "archived"})


def refresh_meta(user_id: int, dataset_id: int) -> Tuple[Optional[dict], Optional[str]]:
    detail, err = get_detail(user_id, dataset_id)
    if err:
        return None, err
    ver = detail.get("current_version_meta") or {}
    path = ver.get("storage_path") or ""
    if not os.path.isfile(path):
        return None, "当前版本文件缺失"
    meta, merr = parse_tabular_meta(path)
    if merr:
        return None, merr

    # 插入新版本号但内容路径不变（刷新元数据视为运维版本）
    ds = detail["dataset"]
    new_version = int(ds["current_version"]) + 1
    # 复制当前文件到新版本目录
    storage, cerr = _copy_source_to_version(
        user_id, dataset_id, new_version, path, os.path.basename(path)
    )
    if cerr:
        return None, cerr
    _, verr = store.insert_dataset_version(
        {
            "dataset_id": dataset_id,
            "version": new_version,
            "storage_path": storage,
            "row_count": meta["row_count"],
            "column_count": meta["column_count"],
            "schema_json": meta["schema_json"],
            "missing_stats_json": meta["missing_stats_json"],
            "preview_json": meta["preview_json"],
            "note": "刷新元数据",
        }
    )
    if verr:
        return None, verr
    uerr = store.update_dataset(user_id, dataset_id, {"current_version": new_version})
    if uerr:
        return None, uerr
    return get_detail(user_id, dataset_id)
