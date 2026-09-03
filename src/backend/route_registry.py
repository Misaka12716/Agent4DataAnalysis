"""集中注册模块化 FastAPI 路由。"""

from __future__ import annotations

import logging

from fastapi import FastAPI


logger = logging.getLogger(__name__)


def register_modular_routes(app: FastAPI) -> None:
    from backend.project_routes import register_project_routes

    register_project_routes(app)

    try:
        from backend.chunked_upload_routes import register_chunked_upload_routes

        register_chunked_upload_routes(app)
    except Exception as exc:  # pragma: no cover
        logger.warning("chunked upload routes not registered: %s", exc)
