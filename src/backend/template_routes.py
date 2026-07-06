# backend/template_routes.py — 模板管理 API

from __future__ import annotations

import os

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse

from backend.jwt_auth import CurrentUser, get_current_user
from backend.project_auth import (
    assert_platform_admin,
    assert_session_access,
    assert_session_project_not_archived,
)
from backend.template_models import TemplateCreateRequest, TemplateUpdateRequest
from db.rbac_schema import PERM_ANALYSIS_CREATE


def register_template_routes(app) -> None:
    """在现有 FastAPI app 上追加模板路由。"""

    @app.post("/template/create")
    async def template_create(
        body: TemplateCreateRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_platform_admin(current_user.user_id)
        from backend.template_service import TemplateService

        resp, err = TemplateService.create_template(body.model_dump())
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(content={"status": "success", "data": resp}, status_code=201)

    @app.get("/template/list")
    async def template_list(
        disease_type: str = "",
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.template_service import TemplateService

        dt = disease_type.strip() or None
        rows, err = TemplateService.list_templates(dt)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(content={"status": "success", "data": rows}, status_code=200)

    @app.get("/template/{template_id}")
    async def template_get(
        template_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.template_service import TemplateService

        resp, err = TemplateService.get_template(template_id)
        if err:
            status = 404 if err == "模板不存在" else 400
            raise HTTPException(status_code=status, detail=err)
        return JSONResponse(content={"status": "success", "data": resp}, status_code=200)

    @app.put("/template/{template_id}")
    async def template_update(
        template_id: int,
        body: TemplateUpdateRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_platform_admin(current_user.user_id)
        from backend.template_service import TemplateService

        resp, err = TemplateService.update_template(template_id, body.model_dump(exclude_none=True))
        if err:
            status = 404 if err == "模板不存在" else 400
            raise HTTPException(status_code=status, detail=err)
        return JSONResponse(content={"status": "success", "data": resp}, status_code=200)

    @app.delete("/template/{template_id}")
    async def template_delete(
        template_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_platform_admin(current_user.user_id)
        from backend.template_service import TemplateService

        ok, err = TemplateService.delete_template(template_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(content={"status": "success", "message": "模板已删除"}, status_code=200)

    @app.post("/template/import")
    async def template_import(current_user: CurrentUser = Depends(get_current_user)):
        assert_platform_admin(current_user.user_id)
        from backend.template_service import TemplateService

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        templates_dir = os.path.join(project_root, "knowledge", "templates")
        result, err = TemplateService.import_templates(templates_dir)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(content={"status": "success", "data": result}, status_code=200)

    @app.post("/analysis/template-run")
    async def analysis_template_run(
        body: dict,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        """模板驱动定量分析（同步，不依赖 LLM）。"""
        import asyncio

        from backend.template_analysis_service import run_template_analysis

        session_id = body.get("session_id", "").strip()
        template_id = int(body.get("template_id") or 0)
        if not session_id or template_id <= 0:
            raise HTTPException(status_code=400, detail="session_id 与 template_id 必填")
        session_user = assert_session_access(session_id, current_user.user_id, PERM_ANALYSIS_CREATE)
        assert_session_project_not_archived(session_user)
        ws = str(session_user.get("workspace_abs_path") or "")
        resp, err = await asyncio.to_thread(
            run_template_analysis,
            session_id,
            template_id,
            ws,
            body.get("file_path"),
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return JSONResponse(content={"status": "success", "data": resp}, status_code=200)
