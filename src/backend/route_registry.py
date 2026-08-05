"""集中注册模块化 FastAPI 路由。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI


logger = logging.getLogger(__name__)


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

    # 统一分片上传 /upload/chunked/*
    try:
        from backend.chunked_upload_routes import register_chunked_upload_routes

        register_chunked_upload_routes(app)
    except Exception as exc:  # pragma: no cover
        logger.warning("chunked upload routes not registered: %s", exc)

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
        logger.warning("psych routes not registered: %s", exc)

    # 2.1.3 数据质量控制可视化：与主后端同源复用 52716。
    try:
        from backend.dq213_routes import register_dq213_routes
        from db.dq213_schema import ensure_dq213_tables
        from utils.mysql_utils import mysql_handler

        schema_ok, schema_error = ensure_dq213_tables(mysql_handler)
        if not schema_ok:
            logger.warning("dq213 schema initialization failed: %s", schema_error)

        register_dq213_routes(
            app,
            static_dir=Path(__file__).resolve().parents[1]
            / "frontend"
            / "web"
            / "dq213",
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("dq213 routes not registered: %s", exc)
