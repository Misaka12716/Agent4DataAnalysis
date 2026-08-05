# backend/chunked_upload_finalize.py
# 分片合并后按 target 调用各域落盘 / 登记逻辑

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Dict, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from backend.chunked_upload_service import deprecated_upload_notice
from backend.project_auth import (
    assert_project_access,
    assert_project_not_archived,
    assert_session_access,
    assert_session_project_not_archived,
)
from backend.project_asset_registry import register_upload, relative_path_for_project_asset
from configs.config import LANGUAGE, RESOURCES_MAX_UPLOAD_MB
from db.rbac_schema import PERM_DATA_UPLOAD
from reader.file_types import classify_file, is_upload_allowed, upload_allowed_extensions
from runtime.factory import ensure_runtime
from utils.upload_naming import allocate_unique_name_in_dir, original_basename, safe_filename
from utils.workspace_manager import init_workspace, resolve_project_root

logger = logging.getLogger(__name__)

SESSION_MAX_FILE_SIZE = 2048 * 1024 * 1024
PROJECT_MAX_FILE_SIZE = 2048 * 1024 * 1024
PSYCH_MAX_FILE_SIZE = 200 * 1024 * 1024


def _param_int(params: Dict[str, Any], key: str) -> Optional[int]:
    raw = params.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"target_params.{key} 必须为整数")


def _param_str(params: Dict[str, Any], key: str, default: Optional[str] = None) -> Optional[str]:
    raw = params.get(key, default)
    if raw is None:
        return default
    return str(raw)


def validate_init_target(
    user_id: int,
    target: str,
    filename: str,
    size: int,
    target_params: Dict[str, Any],
) -> None:
    """init 阶段校验权限、类型、大小；失败抛 HTTPException。"""
    params = dict(target_params or {})

    if target == "session":
        session_id = (_param_str(params, "session_id") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="target_params.session_id 不能为空")
        session_user = assert_session_access(session_id, user_id, PERM_DATA_UPLOAD)
        assert_session_project_not_archived(session_user)
        if not is_upload_allowed(filename):
            ext = os.path.splitext(filename)[1].lower()
            allowed = ", ".join(f".{e}" for e in upload_allowed_extensions())
            raise HTTPException(
                status_code=415,
                detail=f"不支持的文件类型: {ext or '（无扩展名）'}；允许: {allowed}",
            )
        if size > SESSION_MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大（最大 {SESSION_MAX_FILE_SIZE // (1024 * 1024)}MB）",
            )
        return

    if target == "project_raw":
        project_id = _param_int(params, "project_id")
        if project_id is None:
            raise HTTPException(status_code=400, detail="target_params.project_id 不能为空")
        project = assert_project_access(project_id, user_id, PERM_DATA_UPLOAD)
        assert_project_not_archived(project)
        if not is_upload_allowed(filename):
            ext = os.path.splitext(filename)[1].lower()
            allowed = ", ".join(f".{e}" for e in upload_allowed_extensions())
            raise HTTPException(
                status_code=415,
                detail=f"不支持的文件类型: {ext or '（无扩展名）'}；允许: {allowed}",
            )
        if size > PROJECT_MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大（最大 {PROJECT_MAX_FILE_SIZE // (1024 * 1024)}MB）",
            )
        return

    if target in ("resources_file", "resources_dataset", "resources_dataset_version"):
        from backend.resource_classify import is_resource_upload_allowed

        probe = safe_filename(original_basename(filename))
        if not is_resource_upload_allowed(probe):
            raise HTTPException(status_code=415, detail=f"不允许上传的文件类型: {probe}")
        max_bytes = RESOURCES_MAX_UPLOAD_MB * 1024 * 1024
        if size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过大小限制 {RESOURCES_MAX_UPLOAD_MB}MB",
            )
        if target == "resources_file":
            parent_id = _param_int(params, "parent_id")
            from backend.resource_file_service import _validate_parent

            verr = _validate_parent(user_id, parent_id)
            if verr:
                raise HTTPException(status_code=400, detail=verr)
        elif target == "resources_dataset_version":
            dataset_id = _param_int(params, "dataset_id")
            if dataset_id is None:
                raise HTTPException(status_code=400, detail="target_params.dataset_id 不能为空")
            from db import resource_store as store

            ds, err = store.get_dataset(user_id, dataset_id)
            if err:
                raise HTTPException(status_code=400, detail=err)
            if not ds:
                raise HTTPException(status_code=404, detail="数据集不存在")
            if ds.get("status") == "archived":
                raise HTTPException(status_code=400, detail="已归档数据集不可上传新版本，请先恢复")
        return

    if target == "resources_model":
        model_name = (_param_str(params, "model_name") or "").strip()
        if not model_name:
            raise HTTPException(status_code=400, detail="target_params.model_name 不能为空")
        safe = safe_filename(filename, fallback="model.pkl")
        ext = os.path.splitext(safe)[1].lower()
        if ext not in (".pkl", ".joblib"):
            raise HTTPException(status_code=415, detail="仅支持 .pkl / .joblib 模型文件")
        max_bytes = RESOURCES_MAX_UPLOAD_MB * 1024 * 1024
        if size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过大小限制 {RESOURCES_MAX_UPLOAD_MB}MB",
            )
        return

    if target == "psych_ingest":
        dataset_id = _param_int(params, "dataset_id")
        if dataset_id is None:
            raise HTTPException(status_code=400, detail="target_params.dataset_id 不能为空")
        if size > PSYCH_MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="上传文件不能超过 200 MB")
        from backend.psych_data_service import get_dataset

        row, err = get_dataset(dataset_id, user_id)
        if err:
            raise HTTPException(status_code=404 if "不存在" in err else 400, detail=err)
        if not row:
            raise HTTPException(status_code=404, detail="数据集不存在")
        return

    raise HTTPException(status_code=400, detail=f"不支持的 target: {target}")


def _copy_into_session_workspace(session_id: str, safe_name: str, source_path: str, workspace_abs: str) -> None:
    """优先本机 shutil 拷贝；失败则经 runtime 整文件写入。"""
    dest = os.path.join(workspace_abs, safe_name)
    try:
        os.makedirs(workspace_abs, exist_ok=True)
        shutil.copy2(source_path, dest)
        # 确保 runtime 侧可见（LocalRuntime workdir 即 workspace）
        ensure_runtime(session_id)
        if os.path.isfile(dest):
            return
    except Exception:
        logger.debug("shutil copy into workspace failed; fallback runtime write", exc_info=True)

    with open(source_path, "rb") as f:
        data = f.read()
    from utils.workspace_file_ops import write_bytes_file

    if not write_bytes_file(session_id, safe_name, data):
        raise HTTPException(status_code=500, detail="保存文件到工作区失败")


def finalize_session(
    user_id: int, filename: str, merged_path: str, params: Dict[str, Any]
) -> JSONResponse:
    from backend.route_services import _schedule_workspace_snapshot

    session_id = (_param_str(params, "session_id") or "").strip()
    session_user = assert_session_access(session_id, user_id, PERM_DATA_UPLOAD)
    assert_session_project_not_archived(session_user)

    ensure_runtime(session_id)
    workspace_abs = str(session_user.get("workspace_abs_path") or "").strip()
    if not workspace_abs:
        workspace_abs = init_workspace(int(session_user.get("user_id") or user_id), session_id)
    os.makedirs(workspace_abs, exist_ok=True)

    client_original = original_basename(filename)
    allocated = allocate_unique_name_in_dir(workspace_abs, client_original)
    safe_name = allocated.stored_name
    _copy_into_session_workspace(session_id, safe_name, merged_path, workspace_abs)

    _schedule_workspace_snapshot(
        session_id,
        lang=LANGUAGE,
        note=f"已上传文件到工作区：`{safe_name}`（类型: {classify_file(safe_name)}）。",
        input_hint="（上传后尚未发起新一轮分析时可参考下列文件列表）",
    )

    project_id = session_user.get("project_id")
    if project_id:
        try:
            proj_root = resolve_project_root(int(project_id))
            if proj_root:
                rel = relative_path_for_project_asset(
                    proj_root,
                    workspace_abs,
                    session_id,
                    os.path.join(workspace_abs, safe_name),
                )
                register_upload(
                    project_id=int(project_id),
                    session_id=session_id,
                    relative_path=rel,
                    original_filename=client_original,
                    file_category=classify_file(safe_name),
                )
        except Exception:
            pass

    return JSONResponse(
        content={
            "status": "success",
            "message": "文件已写入会话工作区根目录",
            "session_id": session_id,
            "relative_path": safe_name,
            "original_filename": client_original,
            "renamed": allocated.renamed,
            "file_category": classify_file(safe_name),
            "workspace_abs_path": workspace_abs,
        },
        status_code=200,
    )


def finalize_project_raw(
    user_id: int, filename: str, merged_path: str, params: Dict[str, Any]
) -> JSONResponse:
    project_id = _param_int(params, "project_id")
    assert project_id is not None
    project = assert_project_access(project_id, user_id, PERM_DATA_UPLOAD)
    assert_project_not_archived(project)

    project_root = resolve_project_root(project_id) or str(project.get("workspace_abs_path") or "")
    raw_dir = os.path.join(project_root, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    client_original = original_basename(filename)
    allocated = allocate_unique_name_in_dir(raw_dir, client_original)
    safe_name = allocated.stored_name
    dest_path = os.path.join(raw_dir, safe_name)
    try:
        shutil.copy2(merged_path, dest_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存文件失败: {e}")

    relative_path = f"raw/{safe_name}"
    file_category = classify_file(safe_name)
    register_upload(
        project_id=project_id,
        session_id=None,
        relative_path=relative_path,
        original_filename=client_original,
        file_category=file_category,
    )
    return JSONResponse(
        content={
            "status": "success",
            "message": "文件已写入项目 raw 目录",
            "deprecated": True,
            "notice": (
                "项目 raw/ 上传不会自动进入分析链路；请创建会话后使用 "
                "POST /session/copy-from-project-raw 或分片上传 target=session。"
            ),
            "project_id": project_id,
            "relative_path": relative_path,
            "original_filename": client_original,
            "renamed": allocated.renamed,
            "file_category": file_category,
        },
        status_code=200,
    )


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def finalize_resources_file(
    user_id: int, filename: str, merged_path: str, params: Dict[str, Any]
) -> JSONResponse:
    from backend.resource_file_service import upload_file

    parent_id = _param_int(params, "parent_id")
    data, err = upload_file(
        user_id,
        filename,
        _read_bytes(merged_path),
        parent_id=parent_id,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return JSONResponse(content={"status": "success", "data": data}, status_code=201)


def finalize_resources_dataset(
    user_id: int, filename: str, merged_path: str, params: Dict[str, Any]
) -> JSONResponse:
    from backend.resource_dataset_service import create_from_upload

    data, err = create_from_upload(
        user_id,
        filename,
        _read_bytes(merged_path),
        name=_param_str(params, "name"),
        description=_param_str(params, "description"),
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return JSONResponse(content={"status": "success", "data": data}, status_code=201)


def finalize_resources_dataset_version(
    user_id: int, filename: str, merged_path: str, params: Dict[str, Any]
) -> JSONResponse:
    from backend.resource_dataset_service import add_version

    dataset_id = _param_int(params, "dataset_id")
    assert dataset_id is not None
    data, err = add_version(
        user_id,
        dataset_id,
        filename,
        _read_bytes(merged_path),
        note=_param_str(params, "note"),
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return JSONResponse(content={"status": "success", "data": data}, status_code=201)


def finalize_resources_model(
    user_id: int, filename: str, merged_path: str, params: Dict[str, Any]
) -> JSONResponse:
    from backend.model_registry_service import upload_model

    data, err = upload_model(
        user_id,
        filename,
        _read_bytes(merged_path),
        model_name=(_param_str(params, "model_name") or "").strip(),
        model_type=_param_str(params, "model_type"),
        task_type=_param_str(params, "task_type"),
        features=params.get("features"),
        metrics=params.get("metrics"),
        params=params.get("params"),
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return JSONResponse(content={"status": "success", "data": data}, status_code=201)


def finalize_psych_ingest(
    user_id: int, filename: str, merged_path: str, params: Dict[str, Any]
) -> JSONResponse:
    from backend.psych_data_service import ingest_file

    dataset_id = _param_int(params, "dataset_id")
    assert dataset_id is not None
    data, err = ingest_file(
        user_id,
        dataset_id,
        filename,
        _read_bytes(merged_path),
        record_type=_param_str(params, "record_type", "row") or "row",
        patient_key_col=_param_str(params, "patient_key_col"),
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return JSONResponse(content={"status": "success", "data": data}, status_code=201)


_FINALIZERS = {
    "session": finalize_session,
    "project_raw": finalize_project_raw,
    "resources_file": finalize_resources_file,
    "resources_dataset": finalize_resources_dataset,
    "resources_dataset_version": finalize_resources_dataset_version,
    "resources_model": finalize_resources_model,
    "psych_ingest": finalize_psych_ingest,
}


def finalize_upload(
    user_id: int,
    *,
    target: str,
    filename: str,
    merged_path: str,
    target_params: Dict[str, Any],
    upload_id: str,
) -> JSONResponse:
    fn = _FINALIZERS.get(target)
    if not fn:
        raise HTTPException(status_code=400, detail=f"不支持的 target: {target}")
    resp = fn(user_id, filename, merged_path, dict(target_params or {}))
    # 注入 upload_id
    try:
        body = resp.body
        import json

        payload = json.loads(body.decode("utf-8"))
        if isinstance(payload, dict):
            payload["upload_id"] = upload_id
            return JSONResponse(content=payload, status_code=resp.status_code)
    except Exception:
        logger.debug("inject upload_id failed", exc_info=True)
    return resp


def attach_deprecated_fields(content: Dict[str, Any]) -> Dict[str, Any]:
    """给旧整文件上传响应附加 deprecated 标记（保留已有 notice）。"""
    out = dict(content)
    out["deprecated"] = True
    existing = str(out.get("notice") or "").strip()
    notice = deprecated_upload_notice()
    if existing and notice not in existing:
        out["notice"] = f"{existing} {notice}"
    else:
        out["notice"] = existing or notice
    return out
