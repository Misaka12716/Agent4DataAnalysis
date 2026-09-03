# backend/chunked_upload_service.py
# 分片上传暂存：init / put_part / status / merge / abort / TTL 清理

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from configs.config import TEMP_FOLDER

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5MB
MIN_CHUNK_SIZE = 1 * 1024 * 1024  # 1MB
MAX_CHUNK_SIZE = 10 * 1024 * 1024  # 10MB
UPLOAD_TTL_SECONDS = 24 * 3600

VALID_TARGETS = frozenset(
    {
        "session",
        "project_raw",
    }
)

_CHUNKED_NOTICE = (
    "整文件 multipart 上传已 deprecated，请改用 POST /upload/chunked/init 分片协议。"
    "详见 docs/ChunkedUploadFrontend.md。"
)


def deprecated_upload_notice() -> str:
    return _CHUNKED_NOTICE


def chunked_uploads_root() -> str:
    return os.path.join(TEMP_FOLDER, "chunked_uploads")


def _user_root(user_id: int) -> str:
    return os.path.join(chunked_uploads_root(), str(int(user_id)))


def _upload_dir(user_id: int, upload_id: str) -> str:
    return os.path.join(_user_root(user_id), upload_id)


def _meta_path(user_id: int, upload_id: str) -> str:
    return os.path.join(_upload_dir(user_id, upload_id), "meta.json")


def _parts_dir(user_id: int, upload_id: str) -> str:
    return os.path.join(_upload_dir(user_id, upload_id), "parts")


def _part_path(user_id: int, upload_id: str, index: int) -> str:
    return os.path.join(_parts_dir(user_id, upload_id), f"{int(index):05d}")


def _merged_path(user_id: int, upload_id: str) -> str:
    return os.path.join(_upload_dir(user_id, upload_id), "merged")


def clamp_chunk_size(chunk_size: Optional[int]) -> int:
    if chunk_size is None:
        return DEFAULT_CHUNK_SIZE
    try:
        size = int(chunk_size)
    except (TypeError, ValueError):
        return DEFAULT_CHUNK_SIZE
    return max(MIN_CHUNK_SIZE, min(MAX_CHUNK_SIZE, size))


def total_chunks_for(size: int, chunk_size: int) -> int:
    if size <= 0:
        return 0
    return (int(size) + int(chunk_size) - 1) // int(chunk_size)


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_meta(user_id: int, upload_id: str) -> Optional[Dict[str, Any]]:
    path = _meta_path(user_id, upload_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    return meta


def _save_meta(user_id: int, upload_id: str, meta: Dict[str, Any]) -> None:
    _atomic_write_json(_meta_path(user_id, upload_id), meta)


def _scan_uploaded_parts(user_id: int, upload_id: str, total_chunks: int) -> List[int]:
    parts_dir = _parts_dir(user_id, upload_id)
    found: List[int] = []
    if not os.path.isdir(parts_dir):
        return found
    for name in os.listdir(parts_dir):
        if not name.isdigit():
            continue
        idx = int(name)
        if 0 <= idx < total_chunks and os.path.isfile(os.path.join(parts_dir, name)):
            found.append(idx)
    return sorted(set(found))


def _expected_part_size(meta: Dict[str, Any], index: int) -> int:
    size = int(meta["size"])
    chunk_size = int(meta["chunk_size"])
    total = int(meta["total_chunks"])
    if index < 0 or index >= total:
        return 0
    if index == total - 1:
        rem = size - chunk_size * (total - 1)
        return rem if rem > 0 else chunk_size
    return chunk_size


def init_upload(
    user_id: int,
    *,
    filename: str,
    size: int,
    target: str,
    target_params: Optional[Dict[str, Any]] = None,
    chunk_size: Optional[int] = None,
    file_sha256: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[int]]:
    """
    创建分片上传会话。
    返回 (payload, error, http_status)。
    """
    target = (target or "").strip()
    if target not in VALID_TARGETS:
        return None, f"不支持的 target: {target or '（空）'}；允许: {', '.join(sorted(VALID_TARGETS))}", 400

    try:
        size_i = int(size)
    except (TypeError, ValueError):
        return None, "size 必须为非负整数", 400
    if size_i < 0:
        return None, "size 必须为非负整数", 400
    if size_i == 0:
        return None, "不允许上传空文件", 400

    cs = clamp_chunk_size(chunk_size)
    total = total_chunks_for(size_i, cs)
    if total <= 0:
        return None, "total_chunks 无效", 400

    upload_id = uuid.uuid4().hex
    now = time.time()
    meta: Dict[str, Any] = {
        "upload_id": upload_id,
        "user_id": int(user_id),
        "filename": filename or "file",
        "size": size_i,
        "chunk_size": cs,
        "total_chunks": total,
        "target": target,
        "target_params": dict(target_params or {}),
        "file_sha256": (file_sha256 or "").strip().lower() or None,
        "uploaded_parts": [],
        "created_at": now,
        "updated_at": now,
        "status": "uploading",
    }

    os.makedirs(_parts_dir(user_id, upload_id), exist_ok=True)
    _save_meta(user_id, upload_id, meta)

    return (
        {
            "upload_id": upload_id,
            "filename": meta["filename"],
            "size": size_i,
            "chunk_size": cs,
            "total_chunks": total,
            "target": target,
            "target_params": meta["target_params"],
            "expires_in_seconds": UPLOAD_TTL_SECONDS,
        },
        None,
        None,
    )


def get_upload_status(
    user_id: int, upload_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[int]]:
    meta = _load_meta(user_id, upload_id)
    if not meta or int(meta.get("user_id") or -1) != int(user_id):
        return None, "upload_id 不存在或无权访问", 404

    if _is_expired(meta):
        abort_upload(user_id, upload_id)
        return None, "上传会话已过期，请重新 init", 410

    total = int(meta["total_chunks"])
    uploaded = _scan_uploaded_parts(user_id, upload_id, total)
    meta["uploaded_parts"] = uploaded
    meta["updated_at"] = time.time()
    _save_meta(user_id, upload_id, meta)

    missing = [i for i in range(total) if i not in set(uploaded)]
    uploaded_bytes = 0
    for idx in uploaded:
        try:
            uploaded_bytes += os.path.getsize(_part_path(user_id, upload_id, idx))
        except OSError:
            pass

    return (
        {
            "upload_id": upload_id,
            "filename": meta.get("filename"),
            "size": int(meta["size"]),
            "chunk_size": int(meta["chunk_size"]),
            "total_chunks": total,
            "target": meta.get("target"),
            "target_params": meta.get("target_params") or {},
            "status": meta.get("status") or "uploading",
            "uploaded_parts": uploaded,
            "missing_parts": missing,
            "uploaded_bytes": uploaded_bytes,
            "progress": round(uploaded_bytes / max(int(meta["size"]), 1), 6),
            "created_at": meta.get("created_at"),
            "expires_in_seconds": max(
                0, int(UPLOAD_TTL_SECONDS - (time.time() - float(meta.get("created_at") or 0)))
            ),
        },
        None,
        None,
    )


def put_part(
    user_id: int,
    upload_id: str,
    index: int,
    data: bytes,
    *,
    part_sha256: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[int]]:
    meta = _load_meta(user_id, upload_id)
    if not meta or int(meta.get("user_id") or -1) != int(user_id):
        return None, "upload_id 不存在或无权访问", 404
    if _is_expired(meta):
        abort_upload(user_id, upload_id)
        return None, "上传会话已过期，请重新 init", 410
    if meta.get("status") == "completed":
        return None, "上传已完成，不可再写入分片", 409

    try:
        idx = int(index)
    except (TypeError, ValueError):
        return None, "分片 index 无效", 400

    total = int(meta["total_chunks"])
    if idx < 0 or idx >= total:
        return None, f"分片 index 超出范围 [0, {total - 1}]", 400

    expected = _expected_part_size(meta, idx)
    if len(data) != expected:
        return (
            None,
            f"分片大小不匹配: index={idx} 期望 {expected} 字节，实际 {len(data)}",
            400,
        )

    if part_sha256:
        digest = hashlib.sha256(data).hexdigest()
        if digest.lower() != part_sha256.strip().lower():
            return None, "分片校验和不匹配", 400

    parts_dir = _parts_dir(user_id, upload_id)
    os.makedirs(parts_dir, exist_ok=True)
    dest = _part_path(user_id, upload_id, idx)
    tmp = f"{dest}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except OSError as exc:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return None, f"写入分片失败: {exc}", 500

    uploaded = _scan_uploaded_parts(user_id, upload_id, total)
    meta["uploaded_parts"] = uploaded
    meta["updated_at"] = time.time()
    meta["status"] = "uploading"
    _save_meta(user_id, upload_id, meta)

    return (
        {
            "upload_id": upload_id,
            "index": idx,
            "bytes": len(data),
            "received": True,
            "uploaded_parts": uploaded,
            "missing_parts": [i for i in range(total) if i not in set(uploaded)],
        },
        None,
        None,
    )


def merge_parts(
    user_id: int, upload_id: str
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str], Optional[int]]:
    """
    校验分片齐全后流式合并到 staging/merged。
    返回 (merged_path, meta, error, http_status)。
    """
    meta = _load_meta(user_id, upload_id)
    if not meta or int(meta.get("user_id") or -1) != int(user_id):
        return None, None, "upload_id 不存在或无权访问", 404
    if _is_expired(meta):
        abort_upload(user_id, upload_id)
        return None, None, "上传会话已过期，请重新 init", 410

    total = int(meta["total_chunks"])
    uploaded = _scan_uploaded_parts(user_id, upload_id, total)
    missing = [i for i in range(total) if i not in set(uploaded)]
    if missing:
        return None, meta, f"分片未齐全，缺少: {missing[:20]}{'...' if len(missing) > 20 else ''}", 400

    merged = _merged_path(user_id, upload_id)
    tmp = f"{merged}.{uuid.uuid4().hex}.tmp"
    expected_size = int(meta["size"])
    hasher = hashlib.sha256()
    written = 0
    try:
        with open(tmp, "wb") as out:
            for idx in range(total):
                part = _part_path(user_id, upload_id, idx)
                with open(part, "rb") as inp:
                    while True:
                        buf = inp.read(1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)
                        hasher.update(buf)
                        written += len(buf)
            out.flush()
            os.fsync(out.fileno())
        if written != expected_size:
            os.remove(tmp)
            return None, meta, f"合并后大小不匹配: 期望 {expected_size}，实际 {written}", 400
        want = meta.get("file_sha256")
        digest = hasher.hexdigest()
        if want and digest != str(want).lower():
            os.remove(tmp)
            return None, meta, "整文件 SHA256 校验失败", 400
        os.replace(tmp, merged)
    except OSError as exc:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return None, meta, f"合并分片失败: {exc}", 500

    meta["uploaded_parts"] = uploaded
    meta["merged_sha256"] = digest
    meta["updated_at"] = time.time()
    meta["status"] = "merged"
    _save_meta(user_id, upload_id, meta)
    return merged, meta, None, None


def mark_completed(user_id: int, upload_id: str) -> None:
    meta = _load_meta(user_id, upload_id)
    if not meta:
        return
    meta["status"] = "completed"
    meta["updated_at"] = time.time()
    _save_meta(user_id, upload_id, meta)


def abort_upload(user_id: int, upload_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[int]]:
    root = _upload_dir(user_id, upload_id)
    if not os.path.isdir(root):
        return None, "upload_id 不存在或无权访问", 404
    meta = _load_meta(user_id, upload_id)
    if meta and int(meta.get("user_id") or -1) != int(user_id):
        return None, "upload_id 不存在或无权访问", 404
    try:
        shutil.rmtree(root, ignore_errors=True)
    except Exception as exc:
        return None, f"清理失败: {exc}", 500
    return {"upload_id": upload_id, "aborted": True}, None, None


def cleanup_staging(user_id: int, upload_id: str) -> None:
    """complete 成功后清理整个 staging 目录。"""
    root = _upload_dir(user_id, upload_id)
    shutil.rmtree(root, ignore_errors=True)


def _is_expired(meta: Dict[str, Any]) -> bool:
    created = float(meta.get("created_at") or 0)
    return (time.time() - created) > UPLOAD_TTL_SECONDS


def cleanup_expired(now: Optional[float] = None) -> int:
    """清理过期未完成上传，返回删除的 upload 目录数。"""
    root = chunked_uploads_root()
    if not os.path.isdir(root):
        return 0
    ts = float(now if now is not None else time.time())
    removed = 0
    for user_name in os.listdir(root):
        user_dir = os.path.join(root, user_name)
        if not os.path.isdir(user_dir):
            continue
        for upload_id in os.listdir(user_dir):
            meta_path = os.path.join(user_dir, upload_id, "meta.json")
            expired = False
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    created = float(meta.get("created_at") or 0)
                    if ts - created > UPLOAD_TTL_SECONDS:
                        expired = True
                except Exception:
                    # 损坏的 staging 也清掉
                    expired = True
            else:
                # 无 meta：按目录 mtime
                try:
                    mtime = os.path.getmtime(os.path.join(user_dir, upload_id))
                    expired = ts - mtime > UPLOAD_TTL_SECONDS
                except OSError:
                    expired = True
            if expired:
                shutil.rmtree(os.path.join(user_dir, upload_id), ignore_errors=True)
                removed += 1
    return removed
