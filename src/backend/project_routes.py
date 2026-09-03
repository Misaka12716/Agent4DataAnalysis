# backend/project_routes.py — 项目管理 API

from __future__ import annotations

import os

from fastapi import Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from backend.current_user import CurrentUser, get_default_user
from backend.project_auth import (
    assert_project_access,
    assert_project_manage_access,
    assert_project_not_archived,
)
from db.rbac_schema import PERM_DATA_UPLOAD
from backend.project_models import ProjectCreateRequest, ProjectUpdateRequest
from reader.file_types import classify_file, is_upload_allowed, upload_allowed_extensions
from utils.upload_naming import allocate_unique_name_in_dir, original_basename
from utils.workspace_manager import resolve_project_root

MAX_FILE_SIZE = 2048 * 1024 * 1024


def register_project_routes(app) -> None:
    """在现有 FastAPI app 上追加项目管理路由。"""

    @app.post("/project/create")
    async def project_create(
        body: ProjectCreateRequest,
        current_user: CurrentUser = Depends(get_default_user),
    ):
        from backend.project_service import ProjectService

        resp, err = ProjectService.create_project(current_user.user_id, body.name)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(
            content={"status": "success", "msg": "project created", "data": resp},
            status_code=201,
        )

    @app.get("/project/list")
    async def project_list(current_user: CurrentUser = Depends(get_default_user)):
        from backend.project_service import ProjectService

        rows, err = ProjectService.list_projects(current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(
            content={"status": "success", "msg": "query projects success", "data": {"projects": rows}},
            status_code=200,
        )

    @app.get("/project/{project_id}")
    async def project_get(
        project_id: int,
        current_user: CurrentUser = Depends(get_default_user),
    ):
        from backend.project_service import ProjectService

        assert_project_access(project_id, current_user.user_id)
        resp, err = ProjectService.get_project_detail(project_id, current_user.user_id)
        if err:
            status = 404 if err == "项目不存在" else 400
            raise HTTPException(status_code=status, detail=err)
        return JSONResponse(content={"status": "success", "data": resp}, status_code=200)

    @app.put("/project/{project_id}")
    async def project_update(
        project_id: int,
        body: ProjectUpdateRequest,
        current_user: CurrentUser = Depends(get_default_user),
    ):
        from backend.project_service import ProjectService

        assert_project_manage_access(project_id, current_user.user_id)
        resp, err = ProjectService.rename_project(project_id, body.name)
        if err:
            status = 404 if err == "项目不存在" else 400
            raise HTTPException(status_code=status, detail=err)
        return JSONResponse(
            content={"status": "success", "msg": "project updated", "data": resp},
            status_code=200,
        )

    @app.get("/project/{project_id}/tree")
    async def project_tree(
        project_id: int,
        current_user: CurrentUser = Depends(get_default_user),
    ):
        from backend.project_service import ProjectService

        assert_project_access(project_id, current_user.user_id)
        resp, err = ProjectService.get_project_tree(project_id)
        if err:
            status = 404 if err == "项目不存在" else 400
            raise HTTPException(status_code=status, detail=err)
        return JSONResponse(content={"status": "success", "data": resp}, status_code=200)

    @app.post("/project/{project_id}/archive")
    async def project_archive(
        project_id: int,
        current_user: CurrentUser = Depends(get_default_user),
    ):
        from backend.project_service import ProjectService

        assert_project_manage_access(project_id, current_user.user_id)
        resp, err = ProjectService.archive_project(project_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(
            content={"status": "success", "msg": "project archived", "data": resp},
            status_code=200,
        )

    @app.post("/project/{project_id}/restore")
    async def project_restore(
        project_id: int,
        current_user: CurrentUser = Depends(get_default_user),
    ):
        from backend.project_service import ProjectService

        assert_project_manage_access(project_id, current_user.user_id)
        resp, err = ProjectService.restore_project(project_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(
            content={"status": "success", "msg": "project restored", "data": resp},
            status_code=200,
        )

    @app.post("/project/{project_id}/upload")
    async def project_upload(
        project_id: int,
        file: UploadFile = File(...),
        current_user: CurrentUser = Depends(get_default_user),
    ):
        from backend.project_asset_registry import register_upload

        project = assert_project_access(project_id, current_user.user_id, PERM_DATA_UPLOAD)
        assert_project_not_archived(project)

        original_filename = file.filename or ""
        ext = os.path.splitext(original_filename)[1].lower()
        if not is_upload_allowed(original_filename):
            allowed = ", ".join(f".{e}" for e in upload_allowed_extensions())
            raise HTTPException(
                status_code=415,
                detail=f"不支持的文件类型: {ext or '（无扩展名）'}；允许: {allowed}",
            )

        file_size = 0
        for chunk in file.file:
            file_size += len(chunk)
            if file_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件过大（最大 {MAX_FILE_SIZE // (1024 * 1024)}MB）",
                )
        await file.seek(0)

        project_root = resolve_project_root(project_id) or str(project.get("workspace_abs_path") or "")
        raw_dir = os.path.join(project_root, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        client_original = original_basename(original_filename)
        allocated = allocate_unique_name_in_dir(raw_dir, client_original)
        safe_name = allocated.stored_name
        dest_path = os.path.join(raw_dir, safe_name)

        try:
            chunks: list[bytes] = []
            while chunk := await file.read(1024 * 1024):
                chunks.append(chunk)
            with open(dest_path, "wb") as f:
                f.write(b"".join(chunks))
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

        from backend.chunked_upload_finalize import attach_deprecated_fields

        return JSONResponse(
            content=attach_deprecated_fields(
                {
                    "status": "success",
                    "message": "文件已写入项目 raw 目录",
                    "notice": (
                        "项目 raw/ 上传不会自动进入分析链路；请创建会话后使用 "
                        "POST /session/copy-from-project-raw 或分片上传 target=session。"
                    ),
                    "project_id": project_id,
                    "relative_path": relative_path,
                    "original_filename": client_original,
                    "renamed": allocated.renamed,
                    "file_category": file_category,
                }
            ),
            status_code=200,
        )

    @app.get("/project/{project_id}/assets")
    async def project_assets(
        project_id: int,
        current_user: CurrentUser = Depends(get_default_user),
    ):
        from backend.project_service import ProjectService

        assert_project_access(project_id, current_user.user_id)
        assets, err = ProjectService.list_assets(project_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(
            content={"status": "success", "data": {"project_id": project_id, "assets": assets}},
            status_code=200,
        )

    @app.get("/project/{project_id}/sessions")
    async def project_sessions(
        project_id: int,
        current_user: CurrentUser = Depends(get_default_user),
    ):
        from backend.project_service import ProjectService

        assert_project_access(project_id, current_user.user_id)
        sessions, err = ProjectService.list_project_sessions(project_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(
            content={
                "status": "success",
                "msg": "query project sessions success",
                "data": {"project_id": project_id, "sessions": sessions},
            },
            status_code=200,
        )
