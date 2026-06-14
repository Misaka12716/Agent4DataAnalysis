# sandbox/session_manager.py
# 会话级 Cube Sandbox 生命周期：create / connect / pause，sandbox_id 持久化于工作区 meta 文件。

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, Optional

from sandbox.config import (
    CUBE_TEMPLATE_ID,
    E2B_API_KEY,
    E2B_API_URL,
    META_FILENAME,
    SANDBOX_TIMEOUT,
    SANDBOX_WORKDIR,
)

logger = logging.getLogger(__name__)

# 避免 HTTP 代理将本地 CubeAPI 请求转发到代理导致 502。
for _proxy_key in (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "all_proxy",
):
    os.environ.pop(_proxy_key, None)

os.environ.setdefault("E2B_API_URL", E2B_API_URL)
os.environ.setdefault("E2B_API_KEY", E2B_API_KEY)

_sandbox_cache: Dict[str, Any] = {}
_session_locks: Dict[str, threading.Lock] = {}
_cache_guard = threading.Lock()


def _lock_for(session_id: str) -> threading.Lock:
    with _cache_guard:
        if session_id not in _session_locks:
            _session_locks[session_id] = threading.Lock()
        return _session_locks[session_id]


def _meta_path(session_id: str) -> Optional[str]:
    from utils.workspace_manager import resolve_workspace_root

    root = resolve_workspace_root(session_id)
    if not root:
        return None
    return os.path.join(root, META_FILENAME)


def _load_meta(session_id: str) -> Optional[Dict[str, str]]:
    path = _meta_path(session_id)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("sandbox_id"):
            return {
                "sandbox_id": str(data["sandbox_id"]),
                "workdir": str(data.get("workdir") or SANDBOX_WORKDIR),
            }
    except Exception:
        logger.exception("load sandbox meta failed: session_id=%s", session_id)
    return None


def _save_meta(session_id: str, sandbox_id: str, workdir: str = SANDBOX_WORKDIR) -> None:
    path = _meta_path(session_id)
    if not path:
        raise RuntimeError(f"workspace not initialized for session_id={session_id}")
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    payload = {"sandbox_id": sandbox_id, "workdir": workdir}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _import_sandbox_cls():
    from e2b_code_interpreter import Sandbox

    return Sandbox


def _create_sandbox():
    Sandbox = _import_sandbox_cls()
    return Sandbox.create(template=CUBE_TEMPLATE_ID, timeout=SANDBOX_TIMEOUT)


def _connect_sandbox(sandbox_id: str):
    Sandbox = _import_sandbox_cls()
    return Sandbox.connect(sandbox_id=sandbox_id, timeout=SANDBOX_TIMEOUT)


def ensure_sandbox(session_id: str):
    """确保 session 已绑定可用沙箱；不存在则 create，存在则 connect。"""
    lock = _lock_for(session_id)
    with lock:
        cached = _sandbox_cache.get(session_id)
        if cached is not None:
            return cached

        meta = _load_meta(session_id)
        if meta:
            try:
                sb = _connect_sandbox(meta["sandbox_id"])
                _sandbox_cache[session_id] = sb
                return sb
            except Exception:
                logger.warning(
                    "connect sandbox failed, recreating: session_id=%s sandbox_id=%s",
                    session_id,
                    meta.get("sandbox_id"),
                    exc_info=True,
                )

        sb = _create_sandbox()
        _sandbox_cache[session_id] = sb
        _save_meta(session_id, sb.sandbox_id, SANDBOX_WORKDIR)
        logger.info(
            "sandbox created: session_id=%s sandbox_id=%s",
            session_id,
            sb.sandbox_id,
        )
        return sb


def get_sandbox(session_id: str):
    """获取 session 沙箱实例（必要时 connect / create）。"""
    return ensure_sandbox(session_id)


def pause_sandbox(session_id: str) -> None:
    """暂停沙箱以释放 VM 资源；失败时仅记录日志。"""
    lock = _lock_for(session_id)
    with lock:
        sb = _sandbox_cache.pop(session_id, None)
        if sb is None:
            meta = _load_meta(session_id)
            if not meta:
                return
            try:
                sb = _connect_sandbox(meta["sandbox_id"])
            except Exception:
                logger.warning("pause_sandbox connect failed: session_id=%s", session_id)
                return
        try:
            sb.beta_pause()
            logger.info("sandbox paused: session_id=%s", session_id)
        except Exception:
            logger.warning("sandbox pause failed: session_id=%s", session_id, exc_info=True)


def clear_sandbox_cache(session_id: str) -> None:
    """测试辅助：清除内存缓存。"""
    with _lock_for(session_id):
        _sandbox_cache.pop(session_id, None)
