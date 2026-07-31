# backend/resource_routes.py
# 个人资源管理 API：文件空间 / 数据集 / 模型库 + 静态前端挂载

from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.jwt_auth import CurrentUser, get_current_user
from backend.resource_models import (
    DatasetCreateRequest,
    DatasetRollbackRequest,
    DatasetUpdateRequest,
    MkdirRequest,
    ModelPredictRequest,
    MoveFileRequest,
    PromoteDatasetRequest,
)


def _ok(data, status_code: int = 200):
    return JSONResponse(content={"status": "success", "data": data}, status_code=status_code)


def _fail(err: str, status_code: int = 400):
    raise HTTPException(status_code=status_code, detail=err)


def _optional_int(value) -> Optional[int]:
    """解析可选整型 Form/Query（空串视为 None）。"""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in ("", "null", "None"):
        return None
    return int(value)


def _web_resources_dir() -> str:
    # src/backend/resource_routes.py → src/frontend/web/resources
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "frontend", "web", "resources"))


def _web_workbench_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "frontend", "web", "workbench"))


def register_resource_routes(app) -> None:
    """注册个人资源管理路由并挂载静态前端。"""

    # ---------- 静态资源 ----------
    resources_dir = _web_resources_dir()
    workbench_dir = _web_workbench_dir()
    if os.path.isdir(resources_dir):
        app.mount(
            "/static/resources",
            StaticFiles(directory=resources_dir),
            name="resources_static",
        )
    if os.path.isdir(workbench_dir):
        try:
            app.mount(
                "/static/workbench",
                StaticFiles(directory=workbench_dir),
                name="workbench_static",
            )
        except Exception:
            pass  # 可能已被其他模块挂载

    @app.get("/resources/ui", response_class=HTMLResponse)
    async def resources_ui():
        index = os.path.join(resources_dir, "index.html")
        if not os.path.isfile(index):
            raise HTTPException(status_code=404, detail="资源管理前端未部署")
        with open(index, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())

    # ---------- 文件空间 ----------

    @app.get("/resources/files/tree")
    async def files_tree(
        parent_id: Optional[int] = Query(None),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_file_service import list_tree

        data, err = list_tree(current_user.user_id, parent_id)
        if err:
            _fail(err)
        return _ok(data)

    @app.post("/resources/files/mkdir")
    async def files_mkdir(
        body: MkdirRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_file_service import mkdir

        data, err = mkdir(current_user.user_id, body.name, body.parent_id)
        if err:
            _fail(err)
        return _ok(data, 201)

    @app.post("/resources/files/upload")
    async def files_upload(
        file: UploadFile = File(...),
        parent_id: Optional[str] = Form(None),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_file_service import upload_file

        content = await file.read()
        data, err = upload_file(
            current_user.user_id,
            file.filename or "upload.bin",
            content,
            parent_id=_optional_int(parent_id),
            mime=file.content_type,
        )
        if err:
            _fail(err)
        return _ok(data, 201)

    @app.get("/resources/files/{node_id}/download")
    async def files_download(
        node_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_file_service import get_downloadable

        node, err = get_downloadable(current_user.user_id, node_id)
        if err:
            _fail(err, 404 if "不存在" in err or "缺失" in err else 400)
        return FileResponse(
            path=node["storage_path"],
            filename=node["name"],
            media_type=node.get("mime") or "application/octet-stream",
        )

    @app.get("/resources/files/{node_id}/preview")
    async def files_preview(
        node_id: int,
        as_file: bool = Query(False, description="PDF/图片强制以文件流返回"),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_file_service import get_downloadable
        from backend.resource_preview_service import build_preview

        node, err = get_downloadable(current_user.user_id, node_id)
        if err:
            _fail(err, 404 if "不存在" in err or "缺失" in err else 400)

        preview, perr = build_preview(
            node["storage_path"],
            node.get("category") or "other",
            node.get("name") or "",
        )
        if perr:
            _fail(perr)

        if as_file:
            return FileResponse(
                path=node["storage_path"],
                filename=node["name"],
                media_type=node.get("mime") or preview.get("mime") or "application/octet-stream",
            )
        return _ok(preview)

    @app.post("/resources/files/{node_id}/move")
    async def files_move(
        node_id: int,
        body: MoveFileRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_file_service import move_node

        data, err = move_node(current_user.user_id, node_id, body.target_parent_id)
        if err:
            _fail(err)
        return _ok(data)

    @app.delete("/resources/files/{node_id}")
    async def files_delete(
        node_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_file_service import delete_node

        data, err = delete_node(current_user.user_id, node_id)
        if err:
            _fail(err)
        return _ok(data)

    @app.post("/resources/files/{node_id}/promote-dataset")
    async def files_promote_dataset(
        node_id: int,
        body: PromoteDatasetRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import create_from_file

        data, err = create_from_file(
            current_user.user_id,
            node_id,
            name=body.name,
            description=body.description,
        )
        if err:
            _fail(err)
        return _ok(data, 201)

    # ---------- 数据集 ----------

    @app.get("/resources/datasets")
    async def datasets_list(
        status: Optional[str] = Query(None),
        keyword: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import list_datasets

        data, err = list_datasets(
            current_user.user_id, status=status, keyword=keyword, limit=limit, offset=offset
        )
        if err:
            _fail(err)
        return _ok(data)

    @app.post("/resources/datasets")
    async def datasets_create(
        body: DatasetCreateRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import create_from_file

        data, err = create_from_file(
            current_user.user_id,
            body.source_file_id,
            name=body.name,
            description=body.description,
        )
        if err:
            _fail(err)
        return _ok(data, 201)

    @app.post("/resources/datasets/upload")
    async def datasets_upload(
        file: UploadFile = File(...),
        name: Optional[str] = Form(None),
        description: Optional[str] = Form(None),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import create_from_upload

        content = await file.read()
        data, err = create_from_upload(
            current_user.user_id,
            file.filename or "data.csv",
            content,
            name=name,
            description=description,
        )
        if err:
            _fail(err)
        return _ok(data, 201)

    @app.get("/resources/datasets/{dataset_id}")
    async def datasets_get(
        dataset_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import get_detail

        data, err = get_detail(current_user.user_id, dataset_id)
        if err:
            _fail(err, 404 if err == "数据集不存在" else 400)
        return _ok(data)

    @app.get("/resources/datasets/{dataset_id}/preview")
    async def datasets_preview(
        dataset_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import get_preview

        data, err = get_preview(current_user.user_id, dataset_id)
        if err:
            _fail(err, 404 if "不存在" in err else 400)
        return _ok(data)

    @app.get("/resources/datasets/{dataset_id}/download")
    async def datasets_download(
        dataset_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import get_downloadable

        info, err = get_downloadable(current_user.user_id, dataset_id)
        if err:
            _fail(err, 404 if "不存在" in err or "缺失" in err else 400)
        return FileResponse(path=info["path"], filename=info["filename"])

    @app.post("/resources/datasets/{dataset_id}/versions")
    async def datasets_add_version(
        dataset_id: int,
        file: UploadFile = File(...),
        note: Optional[str] = Form(None),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import add_version

        content = await file.read()
        data, err = add_version(
            current_user.user_id,
            dataset_id,
            file.filename or "data.csv",
            content,
            note=note,
        )
        if err:
            _fail(err)
        return _ok(data, 201)

    @app.get("/resources/datasets/{dataset_id}/versions")
    async def datasets_list_versions(
        dataset_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import list_versions

        data, err = list_versions(current_user.user_id, dataset_id)
        if err:
            _fail(err, 404 if "不存在" in err else 400)
        return _ok(data)

    @app.post("/resources/datasets/{dataset_id}/rollback")
    async def datasets_rollback(
        dataset_id: int,
        body: DatasetRollbackRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import rollback

        data, err = rollback(current_user.user_id, dataset_id, body.version)
        if err:
            _fail(err)
        return _ok(data)

    @app.patch("/resources/datasets/{dataset_id}")
    async def datasets_update(
        dataset_id: int,
        body: DatasetUpdateRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import update_dataset

        data, err = update_dataset(
            current_user.user_id,
            dataset_id,
            body.model_dump(exclude_none=True),
        )
        if err:
            _fail(err, 404 if err == "数据集不存在" else 400)
        return _ok(data)

    @app.delete("/resources/datasets/{dataset_id}")
    async def datasets_delete(
        dataset_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import archive_or_delete

        data, err = archive_or_delete(current_user.user_id, dataset_id)
        if err:
            _fail(err, 404 if "不存在" in err else 400)
        return _ok(data)

    @app.post("/resources/datasets/{dataset_id}/refresh-meta")
    async def datasets_refresh_meta(
        dataset_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.resource_dataset_service import refresh_meta

        data, err = refresh_meta(current_user.user_id, dataset_id)
        if err:
            _fail(err)
        return _ok(data)

    # ---------- 模型库 ----------

    @app.get("/resources/models")
    async def models_list(
        keyword: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.model_registry_service import list_models

        data, err = list_models(current_user.user_id, keyword=keyword, limit=limit, offset=offset)
        if err:
            _fail(err)
        return _ok(data)

    @app.post("/resources/models/upload")
    async def models_upload(
        file: UploadFile = File(...),
        model_name: str = Form(...),
        model_type: Optional[str] = Form(None),
        task_type: Optional[str] = Form(None),
        features: Optional[str] = Form(None, description="JSON 数组字符串"),
        metrics: Optional[str] = Form(None, description="JSON 对象字符串"),
        params: Optional[str] = Form(None, description="JSON 对象字符串"),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.model_registry_service import upload_model

        def _parse_json(raw: Optional[str], default=None):
            if not raw:
                return default
            try:
                return json.loads(raw)
            except Exception:
                raise HTTPException(status_code=400, detail=f"无效 JSON: {raw[:80]}")

        content = await file.read()
        data, err = upload_model(
            current_user.user_id,
            file.filename or "model.pkl",
            content,
            model_name=model_name,
            model_type=model_type,
            task_type=task_type,
            features=_parse_json(features),
            metrics=_parse_json(metrics),
            params=_parse_json(params),
        )
        if err:
            _fail(err)
        return _ok(data, 201)

    @app.get("/resources/models/{model_id}")
    async def models_get(
        model_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.model_registry_service import get_model

        data, err = get_model(current_user.user_id, model_id)
        if err:
            _fail(err, 404 if err == "模型不存在" else 400)
        return _ok(data)

    @app.get("/resources/models/{model_id}/download")
    async def models_download(
        model_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.model_registry_service import get_downloadable

        info, err = get_downloadable(current_user.user_id, model_id)
        if err:
            _fail(err, 404 if "不存在" in err or "缺失" in err else 400)
        return FileResponse(path=info["path"], filename=info["filename"])

    @app.delete("/resources/models/{model_id}")
    async def models_delete(
        model_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.model_registry_service import delete_model

        data, err = delete_model(current_user.user_id, model_id)
        if err:
            _fail(err, 404 if "不存在" in err else 400)
        return _ok(data)

    @app.post("/resources/models/{model_id}/predict")
    async def models_predict(
        model_id: int,
        body: ModelPredictRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.model_registry_service import predict

        data, err = predict(current_user.user_id, model_id, body.rows)
        if err:
            _fail(err)
        return _ok(data)
