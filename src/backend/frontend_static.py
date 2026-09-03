# backend/frontend_static.py
# 可选：托管 web/dist 生产构建（存在时启用）

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


logger = logging.getLogger(__name__)

_API_PREFIXES = (
    "project",
    "session",
    "run-analysis",
    "upload",
    "health",
)


def register_frontend_static(app: FastAPI) -> None:
    web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if not web_dist.is_dir():
        logger.info("web/dist not found; frontend static hosting skipped")
        return

    index_html = web_dist / "index.html"
    assets_dir = web_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    async def spa_index():
        if not index_html.is_file():
            raise HTTPException(status_code=404, detail="frontend index missing")
        return FileResponse(index_html)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        first = full_path.split("/", 1)[0] if full_path else ""
        if first in _API_PREFIXES:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = web_dist / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        if not index_html.is_file():
            raise HTTPException(status_code=404, detail="frontend index missing")
        return FileResponse(index_html)

    logger.info("frontend static hosting enabled from %s", web_dist)
