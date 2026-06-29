# sandbox/session_manager.py
# 会话级 Cube Sandbox 生命周期：create / connect / pause，sandbox_id 持久化于工作区 meta 文件。

from __future__ import annotations

import json
import logging
import os
import threading
import time
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

# 本地 Cube Sandbox 使用 mkcert HTTPS 连接 envd 时需要 CA 证书。
for _ssl_cert in (
    os.getenv("SSL_CERT_FILE"),
    "/root/.local/share/mkcert/rootCA.pem",
    os.path.expanduser("~/.local/share/mkcert/rootCA.pem"),
):
    if _ssl_cert and os.path.isfile(_ssl_cert):
        os.environ.setdefault("SSL_CERT_FILE", _ssl_cert)
        break

_sandbox_cache: Dict[str, Any] = {}
_envd_reachable: Dict[str, bool] = {}
_envd_warned: set[str] = set()
_sandbox_unavailable_warned: bool = False
_create_cooldown_until: float = 0.0
_CREATE_COOLDOWN_SEC = 60.0
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


def _verify_envd_once(sandbox, session_id: str) -> bool:
    """探测 envd 是否可达；结果按 session 缓存，避免每次操作重复 HTTP 探测。"""
    if session_id in _envd_reachable:
        return _envd_reachable[session_id]
    try:
        ok = bool(sandbox.is_running())
    except Exception as exc:
        logger.debug("envd unreachable session=%s: %s", session_id, exc)
        ok = False
    _envd_reachable[session_id] = ok
    if not ok and session_id not in _envd_warned:
        _envd_warned.add(session_id)
        logger.warning(
            "CubeProxy/envd unreachable for session=%s; using local workspace mirror. "
            "To restore sandbox execution: sudo systemctl restart cube-sandbox-cube-proxy.service",
            session_id,
        )
    return ok


def is_envd_reachable(session_id: str) -> bool:
    """envd 是否可用；未知时先 ensure_sandbox 再返回缓存结果。"""
    if session_id in _envd_reachable:
        return _envd_reachable[session_id]
    ensure_sandbox(session_id)
    return _envd_reachable.get(session_id, False)


def _warn_sandbox_unavailable(exc: Exception) -> None:
    global _sandbox_unavailable_warned
    if _sandbox_unavailable_warned:
        return
    _sandbox_unavailable_warned = True
    logger.warning(
        "Cube Sandbox control plane unavailable (%s); using local workspace mirror. "
        "Run: bash scripts/diagnose-cube-sandbox.sh",
        exc,
    )


def try_ensure_sandbox(session_id: str) -> bool:
    """
    尽力绑定沙箱；控制面创建失败时返回 False（不抛异常），调用方应使用本地工作区。
    """
    try:
        ensure_sandbox(session_id)
        return True
    except Exception as exc:
        _warn_sandbox_unavailable(exc)
        return False


def is_sandbox_bound(session_id: str) -> bool:
    """当前 session 是否已成功绑定沙箱实例。"""
    return session_id in _sandbox_cache


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
                _verify_envd_once(sb, session_id)
                _sandbox_cache[session_id] = sb
                return sb
            except Exception:
                logger.warning(
                    "connect sandbox failed, recreating: session_id=%s sandbox_id=%s",
                    session_id,
                    meta.get("sandbox_id"),
                    exc_info=True,
                )

        _envd_reachable.pop(session_id, None)
        _envd_warned.discard(session_id)
        global _create_cooldown_until
        if time.monotonic() < _create_cooldown_until:
            raise RuntimeError(
                "sandbox create skipped (recent control-plane failure; "
                f"retry after {_CREATE_COOLDOWN_SEC}s)"
            )
        try:
            sb = _create_sandbox()
        except Exception as exc:
            _create_cooldown_until = time.monotonic() + _CREATE_COOLDOWN_SEC
            raise exc
        _verify_envd_once(sb, session_id)
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
        _envd_reachable.pop(session_id, None)
        _envd_warned.discard(session_id)
