# runtime — 会话级代码执行与工作区文件管理（E2B 风格 API，默认本地 subprocess）

from runtime.types import CommandResult, WriteInfo

__all__ = ["ensure_runtime", "release_runtime", "CommandResult", "WriteInfo"]


def __getattr__(name: str):
    if name == "ensure_runtime":
        from runtime.factory import ensure_runtime

        return ensure_runtime
    if name == "release_runtime":
        from runtime.factory import release_runtime

        return release_runtime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
