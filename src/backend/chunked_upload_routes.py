# backend/chunked_upload_routes.py
# 统一分片上传协议：/upload/chunked/*

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.chunked_upload_finalize import finalize_upload, validate_init_target
from backend.chunked_upload_service import (
    abort_upload,
    cleanup_expired,
    cleanup_staging,
    get_upload_status,
    init_upload,
    mark_completed,
    merge_parts,
    put_part,
)
from backend.jwt_auth import CurrentUser, get_current_user

logger = logging.getLogger(__name__)


class ChunkedInitRequest(BaseModel):
    filename: str
    size: int = Field(..., ge=0)
    target: str
    target_params: Dict[str, Any] = Field(default_factory=dict)
    chunk_size: Optional[int] = None
    file_sha256: Optional[str] = None


def _ok(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content={"status": "success", "data": data}, status_code=status_code)


def _raise(err: str, status_code: int = 400) -> None:
    raise HTTPException(status_code=status_code, detail=err)


def register_chunked_upload_routes(app) -> None:
    @app.post("/upload/chunked/init")
    async def chunked_init(
        body: ChunkedInitRequest,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        cleanup_expired()
        validate_init_target(
            current_user.user_id,
            body.target,
            body.filename,
            body.size,
            body.target_params or {},
        )
        data, err, code = init_upload(
            current_user.user_id,
            filename=body.filename,
            size=body.size,
            target=body.target,
            target_params=body.target_params or {},
            chunk_size=body.chunk_size,
            file_sha256=body.file_sha256,
        )
        if err:
            _raise(err, code or 400)
        return _ok(data, 201)

    @app.put("/upload/chunked/{upload_id}/parts/{index}")
    async def chunked_put_part(
        upload_id: str,
        index: int,
        chunk: UploadFile = File(...),
        part_sha256: Optional[str] = Form(None),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        data = await chunk.read()
        payload, err, code = put_part(
            current_user.user_id,
            upload_id,
            index,
            data,
            part_sha256=part_sha256,
        )
        if err:
            _raise(err, code or 400)
        return _ok(payload)

    @app.get("/upload/chunked/{upload_id}")
    async def chunked_status(
        upload_id: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        payload, err, code = get_upload_status(current_user.user_id, upload_id)
        if err:
            _raise(err, code or 400)
        return _ok(payload)

    @app.post("/upload/chunked/{upload_id}/complete")
    async def chunked_complete(
        upload_id: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        merged_path, meta, err, code = merge_parts(current_user.user_id, upload_id)
        if err:
            _raise(err, code or 400)
        assert merged_path and meta
        try:
            resp = finalize_upload(
                current_user.user_id,
                target=str(meta.get("target") or ""),
                filename=str(meta.get("filename") or "file"),
                merged_path=merged_path,
                target_params=dict(meta.get("target_params") or {}),
                upload_id=upload_id,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("chunked complete finalize failed: upload_id=%s", upload_id)
            raise HTTPException(status_code=500, detail=f"合并后落盘失败: {exc}") from exc

        mark_completed(current_user.user_id, upload_id)
        cleanup_staging(current_user.user_id, upload_id)
        return resp

    @app.delete("/upload/chunked/{upload_id}")
    async def chunked_abort(
        upload_id: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        payload, err, code = abort_upload(current_user.user_id, upload_id)
        if err:
            _raise(err, code or 400)
        return _ok(payload)
