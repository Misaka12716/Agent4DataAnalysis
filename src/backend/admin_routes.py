# backend/admin_routes.py — 平台管理员用户管理 API

from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from backend.jwt_auth import CurrentUser, get_current_user
from backend.project_auth import assert_platform_admin
from backend.rbac_models import AdminCreateUserRequest, AdminUpdateUserRequest
from db.rbac_schema import PLATFORM_ROLE_ADMIN, PLATFORM_ROLE_USER, USER_STATUS_ACTIVE, USER_STATUS_BLOCKED
from db.rbac_store import RbacStore


def register_admin_routes(app) -> None:
    @app.post("/admin/users")
    async def admin_create_user(
        body: AdminCreateUserRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_platform_admin(current_user.user_id)
        phone = body.phone.strip()
        username = body.username.strip()
        if not phone or not username:
            raise HTTPException(status_code=400, detail="username 与 phone 必填")

        existing, err = RbacStore.get_user_by_phone(phone)
        if err:
            raise HTTPException(status_code=500, detail=err)
        if existing:
            raise HTTPException(status_code=409, detail="手机号已注册")

        role = body.platform_role.strip().lower()
        if role not in (PLATFORM_ROLE_ADMIN, PLATFORM_ROLE_USER):
            raise HTTPException(status_code=400, detail="platform_role 无效")
        status = body.status.strip().lower()
        if status not in (USER_STATUS_ACTIVE, USER_STATUS_BLOCKED):
            raise HTTPException(status_code=400, detail="status 无效")

        user_id, err = RbacStore.create_user(username, phone, role, status)
        if err or not user_id:
            raise HTTPException(status_code=500, detail=err or "创建用户失败")
        user, _ = RbacStore.get_user(user_id)
        return JSONResponse(
            content={"status": "success", "msg": "user created", "data": user},
            status_code=201,
        )

    @app.get("/admin/users")
    async def admin_list_users(
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_platform_admin(current_user.user_id)
        rows, total, err = RbacStore.list_users(offset, limit)
        if err:
            raise HTTPException(status_code=500, detail=err)
        return JSONResponse(
            content={
                "status": "success",
                "data": {"users": rows, "total": total, "offset": offset, "limit": limit},
            },
            status_code=200,
        )

    @app.get("/admin/users/{user_id}")
    async def admin_get_user(
        user_id: int,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_platform_admin(current_user.user_id)
        user, err = RbacStore.get_user(user_id)
        if err:
            raise HTTPException(status_code=500, detail=err)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        return JSONResponse(content={"status": "success", "data": user}, status_code=200)

    @app.put("/admin/users/{user_id}")
    async def admin_update_user(
        user_id: int,
        body: AdminUpdateUserRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        assert_platform_admin(current_user.user_id)
        user, err = RbacStore.get_user(user_id)
        if err:
            raise HTTPException(status_code=500, detail=err)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        fields = body.model_dump(exclude_none=True)
        if "platform_role" in fields:
            role = fields["platform_role"].strip().lower()
            if role not in (PLATFORM_ROLE_ADMIN, PLATFORM_ROLE_USER):
                raise HTTPException(status_code=400, detail="platform_role 无效")
            fields["platform_role"] = role
        if "status" in fields:
            status = fields["status"].strip().lower()
            if status not in (USER_STATUS_ACTIVE, USER_STATUS_BLOCKED):
                raise HTTPException(status_code=400, detail="status 无效")
            fields["status"] = status

        ok, err = RbacStore.update_user(user_id, fields)
        if err or not ok:
            raise HTTPException(status_code=500, detail=err or "更新失败")
        updated, _ = RbacStore.get_user(user_id)
        return JSONResponse(
            content={"status": "success", "msg": "user updated", "data": updated},
            status_code=200,
        )
