from __future__ import annotations

import os
from typing import List, Literal, Optional

from runtime.types import WriteInfo
from runtime.validation import resolve_safe_absolute_path_in_workdir
from utils.workspace_manager import is_safe_relative_path


class LocalFilesystem:
    def __init__(self, session_id: str, workdir: str) -> None:
        self._session_id = session_id
        self._workdir = workdir

    def _abs_path(self, rel: str) -> Optional[str]:
        return resolve_safe_absolute_path_in_workdir(self._workdir, rel)

    def write(self, path: str, data: bytes | str) -> WriteInfo | None:
        rel = (path or "").replace("\\", "/").lstrip("/")
        if not is_safe_relative_path(rel):
            return None
        abs_path = self._abs_path(rel)
        if not abs_path:
            return None
        payload = data.encode("utf-8") if isinstance(data, str) else data
        try:
            parent = os.path.dirname(abs_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(abs_path, "wb") as f:
                f.write(payload)
            return WriteInfo(path=rel, bytes_written=len(payload))
        except OSError:
            return None

    def read(
        self, path: str, format: Literal["bytes", "text"] = "bytes"
    ) -> bytes | str | None:
        rel = (path or "").replace("\\", "/").lstrip("/")
        abs_path = self._abs_path(rel)
        if not abs_path or not os.path.isfile(abs_path):
            return None
        try:
            with open(abs_path, "rb") as f:
                raw = f.read()
        except OSError:
            return None
        if format == "text":
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")
        return raw

    def exists(self, path: str) -> bool:
        rel = (path or "").replace("\\", "/").lstrip("/")
        abs_path = self._abs_path(rel)
        return bool(abs_path and os.path.exists(abs_path))

    def list(self, dir_path: str = ".", depth: int = 1) -> List[str]:
        rel_dir = "" if dir_path in (".", "./", "") else dir_path.strip("/")
        base = self._abs_path(rel_dir or ".")
        if not base or not os.path.isdir(base):
            return []

        found: List[str] = []
        max_depth = max(1, int(depth))

        def _walk(current_abs: str, current_rel: str, level: int) -> None:
            if level > max_depth:
                return
            try:
                names = sorted(os.listdir(current_abs))
            except OSError:
                return
            for name in names:
                if name.startswith("."):
                    continue
                child_abs = os.path.join(current_abs, name)
                child_rel = f"{current_rel}/{name}" if current_rel else name
                if os.path.isdir(child_abs):
                    _walk(child_abs, child_rel, level + 1)
                elif os.path.isfile(child_abs):
                    found.append(child_rel.replace("\\", "/"))

        _walk(base, rel_dir, 1)
        return sorted(set(found))
