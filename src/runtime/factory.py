from __future__ import annotations

import logging
from typing import Dict, Union

from runtime.config import is_sandbox_backend_enabled
from runtime.local.runtime import LocalRuntime
from runtime.sandbox_adapter import SandboxRuntimeAdapter

logger = logging.getLogger(__name__)

RuntimeHandle = Union[LocalRuntime, SandboxRuntimeAdapter]

_cache: Dict[str, RuntimeHandle] = {}


def ensure_runtime(session_id: str) -> RuntimeHandle:
    """获取会话执行运行时；沙箱不可用时自动降级为 LocalRuntime。"""
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("session_id 不能为空")

    cached = _cache.get(sid)
    if cached is not None:
        return cached

    runtime: RuntimeHandle
    if is_sandbox_backend_enabled():
        sandbox_rt = SandboxRuntimeAdapter.bind(sid)
        if sandbox_rt is not None:
            runtime = sandbox_rt
        else:
            logger.warning(
                "sandbox backend unavailable for session=%s; falling back to local runtime",
                sid,
            )
            runtime = LocalRuntime.bind(sid)
    else:
        runtime = LocalRuntime.bind(sid)

    _cache[sid] = runtime
    return runtime


def release_runtime(session_id: str) -> None:
    """释放会话运行时（沙箱模式下 pause VM）。"""
    sid = (session_id or "").strip()
    runtime = _cache.pop(sid, None)
    if runtime is not None:
        try:
            runtime.close()
        except Exception:
            logger.debug("runtime close failed: session=%s", sid, exc_info=True)


def clear_runtime_cache(session_id: str) -> None:
    """测试辅助：清除运行时缓存。"""
    _cache.pop((session_id or "").strip(), None)
