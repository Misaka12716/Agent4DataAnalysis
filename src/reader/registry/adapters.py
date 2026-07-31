"""将既有 digest_* 函数适配为 FormatHandler。"""

from __future__ import annotations

from typing import Any, Dict

from reader.handlers.fallback import digest_binary_file
from reader.handlers.image import digest_image_file
from reader.handlers.table import digest_table_file
from reader.handlers.text import digest_text_file


class _FnHandler:
    def __init__(self, handler_id: str, fn) -> None:
        self.handler_id = handler_id
        self._fn = fn

    def digest(self, workspace_root: str, relative_path: str, **kwargs: Any) -> Dict[str, Any]:
        return self._fn(workspace_root, relative_path, **kwargs)


def wrap_legacy_handlers() -> Dict[str, _FnHandler]:
    return {
        "table": _FnHandler("table", digest_table_file),
        "image": _FnHandler("image", digest_image_file),
        "text": _FnHandler("text", digest_text_file),
        "binary": _FnHandler("binary", digest_binary_file),
    }
