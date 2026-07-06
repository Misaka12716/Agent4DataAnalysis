# backend/member_routes.py — 项目成员、文件操作与任务占位 API

from __future__ import annotations

import os

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from backend.jwt_auth import CurrentUser, get_current_user
from backend.project_auth import (
    assert_member_manage_access,
    assert_project_access,
    assert_project_not_archived,
)
from backend.rbac_models import (
    AddMemberRequest,
    CreateTaskRequest,
    DeleteAssetRequest,
    UpdateMemberRequest,
)
from db.rbac_schema import (
    DEFAULT_MEMBER_PERMISSIONS,
    PERM_DATA_DELETE,
    PERM_DATA_DOWNLOAD,
    PROJECT_ROLE_MANAGER,
    PROJECT_ROLE_MEMBER,
    TASK_TYPE_TO_PERMISSION,
    VALID_PROJECT_ROLES,
    VALID_TASK_TYPES,
)
from db.rbac_store import RbacStore
from utils.workspace_manager import resolve_project_root


def register_member_routes(app) -> None:
    @app.get("/project/{project_id}/members")
    async def list_members(
        project_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_member_manage_access(project_id, current_user.user_id)
        members, err = RbacStore.list_members(project_id)
        if err:
            raise HTTPException(status_code=500, detail=err)
        return JSONResponse(
            content={"status": "success", "data": {"project_id": project_id, "members": members}},
            status_code=200,
        )

    @app.post("/project/{project_id}/members")
    async def add_member(
        project_id: int,
        body: AddMemberRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_member_manage_access(project_id, current_user.user_id)
        role = body.role.strip().lower()
        if role not in VALID_PROJECT_ROLES:
            raise HTTPException(status_code=400, detail="role 无效")
        user, err = RbacStore.get_user(body.user_id)
        if err:
            raise HTTPException(status_code=500, detail=err)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        perms = body.permissions if body.permissions else list(DEFAULT_MEMBER_PERMISSIONS)
        member_id, err = RbacStore.add_member(project_id, body.user_id, role, perms)
        if err:
            status = 409 if "已存在" in str(err) else 400
            raise HTTPException(status_code=status, detail=err)
        return JSONResponse(
            content={
                "status": "success",
                "msg": "member added",
                "data": {"member_id": member_id, "project_id": project_id, "user_id": body.user_id},
            },
            status_code=201,
        )

    @app.put("/project/{project_id}/members/{member_user_id}")
    async def update_member(
        project_id: int,
        member_user_id: int,
        body: UpdateMemberRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_member_manage_access(project_id, current_user.user_id)
        existing, err = RbacStore.get_member(project_id, member_user_id)
        if err:
            raise HTTPException(status_code=500, detail=err)
        if not existing:
            raise HTTPException(status_code=404, detail="成员不存在")

        role = body.role.strip().lower() if body.role else None
        if role and role not in VALID_PROJECT_ROLES:
            raise HTTPException(status_code=400, detail="role 无效")

        ok, err = RbacStore.update_member(project_id, member_user_id, role, body.permissions)
        if err or not ok:
            raise HTTPException(status_code=500, detail=err or "更新失败")
        updated, _ = RbacStore.get_member(project_id, member_user_id)
        return JSONResponse(
            content={"status": "success", "msg": "member updated", "data": updated},
            status_code=200,
        )

    @app.delete("/project/{project_id}/members/{member_user_id}")
    async def remove_member(
        project_id: int,
        member_user_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_member_manage_access(project_id, current_user.user_id)
        existing, err = RbacStore.get_member(project_id, member_user_id)
        if err:
            raise HTTPException(status_code=500, detail=err)
        if not existing:
            raise HTTPException(status_code=404, detail="成员不存在")
        ok, err = RbacStore.remove_member(project_id, member_user_id)
        if err or not ok:
            raise HTTPException(status_code=500, detail=err or "移除失败")
        return JSONResponse(content={"status": "success", "msg": "member removed"}, status_code=200)

    @app.get("/project/{project_id}/download")
    async def download_asset(
        project_id: int,
        relative_path: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        project = assert_project_access(project_id, current_user.user_id, PERM_DATA_DOWNLOAD)
        rel = relative_path.strip().lstrip("/")
        if not rel or ".." in rel.split("/"):
            raise HTTPException(status_code=400, detail="relative_path 无效")

        project_root = resolve_project_root(project_id) or str(project.get("workspace_abs_path") or "")
        abs_path = os.path.normpath(os.path.join(project_root, rel))
        if not abs_path.startswith(os.path.normpath(project_root)):
            raise HTTPException(status_code=400, detail="路径越界")
        if not os.path.isfile(abs_path):
            raise HTTPException(status_code=404, detail="文件不存在")

        filename = os.path.basename(abs_path)
        return FileResponse(abs_path, filename=filename)

    @app.delete("/project/{project_id}/assets")
    async def delete_asset(
        project_id: int,
        body: DeleteAssetRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        project = assert_project_access(project_id, current_user.user_id, PERM_DATA_DELETE)
        assert_project_not_archived(project)

        rel = body.relative_path.strip().lstrip("/")
        if not rel or ".." in rel.split("/"):
            raise HTTPException(status_code=400, detail="relative_path 无效")

        project_root = resolve_project_root(project_id) or str(project.get("workspace_abs_path") or "")
        abs_path = os.path.normpath(os.path.join(project_root, rel))
        if not abs_path.startswith(os.path.normpath(project_root)):
            raise HTTPException(status_code=400, detail="路径越界")
        if os.path.isfile(abs_path):
            try:
                os.remove(abs_path)
            except OSError as e:
                raise HTTPException(status_code=500, detail=f"删除文件失败: {e}")

        RbacStore.delete_asset(project_id, rel)
        return JSONResponse(
            content={"status": "success", "msg": "asset deleted", "relative_path": rel},
            status_code=200,
        )

    @app.post("/project/{project_id}/tasks")
    async def create_task(
        project_id: int,
        body: CreateTaskRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        task_type = body.task_type.strip().lower()
        if task_type not in VALID_TASK_TYPES:
            raise HTTPException(status_code=400, detail="task_type 无效")

        perm = TASK_TYPE_TO_PERMISSION.get(task_type)
        project = assert_project_access(project_id, current_user.user_id, perm)
        assert_project_not_archived(project)

        task_id, err = RbacStore.create_task(
            project_id,
            task_type,
            current_user.user_id,
            session_id=body.session_id,
            payload=body.payload,
        )
        if err or not task_id:
            raise HTTPException(status_code=500, detail=err or "创建任务失败")
        return JSONResponse(
            content={
                "status": "success",
                "msg": "任务已登记，执行引擎待接入",
                "data": {"task_id": task_id, "status": "pending", "task_type": task_type},
            },
            status_code=201,
        )

    @app.get("/project/{project_id}/tasks")
    async def list_tasks(
        project_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_project_access(project_id, current_user.user_id)
        tasks, err = RbacStore.list_tasks(project_id)
        if err:
            raise HTTPException(status_code=500, detail=err)
        return JSONResponse(
            content={"status": "success", "data": {"project_id": project_id, "tasks": tasks}},
            status_code=200,
        )

    @app.get("/project/{project_id}/tasks/{task_id}")
    async def get_task(
        project_id: int,
        task_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_project_access(project_id, current_user.user_id)
        task, err = RbacStore.get_task(task_id)
        if err:
            raise HTTPException(status_code=500, detail=err)
        if not task or int(task.get("project_id") or 0) != project_id:
            raise HTTPException(status_code=404, detail="任务不存在")
        return JSONResponse(content={"status": "success", "data": task}, status_code=200)
