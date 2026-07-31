"""集中注册模块化 FastAPI 路由。"""

from __future__ import annotations

from fastapi import FastAPI


def register_modular_routes(app: FastAPI) -> None:
    from backend.admin_routes import register_admin_routes
    from backend.member_routes import register_member_routes
    from backend.project_routes import register_project_routes
    from backend.clinical_routes import register_clinical_routes
    from backend.template_routes import register_template_routes

    register_project_routes(app)
    register_member_routes(app)
    register_admin_routes(app)
    register_template_routes(app)
    register_clinical_routes(app)

    # 个人资源管理：文件空间 / 数据集 / 模型库
    try:
        from backend.resource_routes import register_resource_routes

        register_resource_routes(app)
    except Exception as exc:  # pragma: no cover
        import logging

        logging.getLogger(__name__).warning("resource routes not registered: %s", exc)

    # 2.2.10 数据分析工作台（不改 server.py，从这里挂载）
    try:
        from backend.workbench_routes import register_workbench_routes

        register_workbench_routes(app)
    except Exception as exc:  # pragma: no cover
        import logging

        logging.getLogger(__name__).warning("workbench routes not registered: %s", exc)

    # 精神专科多维度分析 /psych/*
    try:
        from backend.psych_routes import register_psych_routes

        register_psych_routes(app)
    except Exception as exc:  # pragma: no cover
        import logging

        logging.getLogger(__name__).warning("psych routes not registered: %s", exc)
