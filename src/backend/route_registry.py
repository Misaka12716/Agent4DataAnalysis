"""集中注册模块化 FastAPI 路由。"""

from __future__ import annotations

from fastapi import FastAPI


def register_modular_routes(app: FastAPI) -> None:
    from backend.admin_routes import register_admin_routes
    from backend.member_routes import register_member_routes
    from backend.project_routes import register_project_routes
    from backend.template_routes import register_template_routes

    register_project_routes(app)
    register_member_routes(app)
    register_admin_routes(app)
    register_template_routes(app)
