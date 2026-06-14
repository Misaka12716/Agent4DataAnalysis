# sandbox/files.py
# 沙箱文件读写与同步到本地工作区镜像（供 Reader / workspace-tree 使用）。

from __future__ import annotations

import os
from typing import List, Optional

from sandbox.config import META_FILENAME, SANDBOX_LIST_DEPTH, SANDBOX_WORKDIR
from sandbox.session_manager import ensure_sandbox, get_sandbox
from utils.workspace_manager import is_safe_relative_path, resolve_workspace_root


def remote_path(relative_path: str) -> str:
    rel = (relative_path or "").replace("\\", "/").lstrip("/")
    base = SANDBOX_WORKDIR.rstrip("/")
    return f"{base}/{rel}" if rel else base


def _entry_name(entry) -> str:
    if hasattr(entry, "name"):
        return str(entry.name)
    if isinstance(entry, dict):
        return str(entry.get("name") or "")
    return ""


def _entry_path(entry) -> str:
    if hasattr(entry, "path"):
        return str(entry.path)
    if isinstance(entry, dict):
        return str(entry.get("path") or "")
    return ""


def _entry_is_dir(entry) -> bool:
    typ = getattr(entry, "type", None)
    if typ is None and isinstance(entry, dict):
        typ = entry.get("type")
    if typ is not None:
        return str(typ).lower() in ("dir", "directory", "folder")
    return False


def list_files(session_id: str) -> List[str]:
    """递归列出沙箱工作目录下的文件相对路径（不含目录）。"""
    sandbox = get_sandbox(session_id)
    workdir = SANDBOX_WORKDIR.rstrip("/")
    found: List[str] = []

    def _walk(dir_path: str) -> None:
        try:
            entries = sandbox.files.list(dir_path, depth=1)
        except Exception:
            return
        for entry in entries or []:
            name = _entry_name(entry)
            if not name or name.startswith("."):
                continue
            full = _entry_path(entry) or f"{dir_path.rstrip('/')}/{name}"
            if _entry_is_dir(entry):
                _walk(full)
            else:
                rel = full
                if rel.startswith(workdir + "/"):
                    rel = rel[len(workdir) + 1 :]
                elif rel == workdir:
                    continue
                rel = rel.replace("\\", "/")
                if rel and rel != META_FILENAME:
                    found.append(rel)

    _walk(workdir)
    return sorted(set(found))


def write_bytes(session_id: str, relative_path: str, data: bytes) -> bool:
    if not is_safe_relative_path(relative_path):
        return False
    try:
        sandbox = ensure_sandbox(session_id)
        sandbox.files.write(remote_path(relative_path), data)
        return True
    except Exception:
        return False


def write_text(session_id: str, relative_path: str, content: str) -> bool:
    return write_bytes(session_id, relative_path, (content or "").encode("utf-8"))


def read_bytes(session_id: str, relative_path: str) -> Optional[bytes]:
    if not is_safe_relative_path(relative_path):
        return None
    try:
        sandbox = get_sandbox(session_id)
        raw = sandbox.files.read(remote_path(relative_path), format="bytes")
        return bytes(raw)
    except Exception:
        return None


def read_text(session_id: str, relative_path: str) -> Optional[str]:
    data = read_bytes(session_id, relative_path)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def sync_to_local(session_id: str) -> Optional[str]:
    """
    将沙箱工作目录文件同步到本地工作区镜像。
    返回本地工作区绝对路径；失败返回 None。
    """
    root = resolve_workspace_root(session_id)
    if not root:
        return None
    os.makedirs(root, exist_ok=True)

    try:
        sandbox = get_sandbox(session_id)
        workdir = SANDBOX_WORKDIR.rstrip("/")
        entries = sandbox.files.list(workdir, depth=SANDBOX_LIST_DEPTH)
    except Exception:
        # 回退：按 list_files 逐文件拉取
        entries = None

    rel_paths: List[str] = []
    if entries:
        for entry in entries or []:
            full = _entry_path(entry)
            if not full or _entry_is_dir(entry):
                continue
            rel = full
            if rel.startswith(workdir + "/"):
                rel = rel[len(workdir) + 1 :]
            elif rel == workdir:
                continue
            rel = rel.replace("\\", "/")
            if rel and not rel.startswith(".") and rel != META_FILENAME:
                rel_paths.append(rel)
    else:
        rel_paths = list_files(session_id)

    for rel in rel_paths:
        if not is_safe_relative_path(rel):
            continue
        try:
            raw = sandbox.files.read(remote_path(rel), format="bytes")
        except Exception:
            continue
        local_abs = os.path.join(root, rel.replace("/", os.sep))
        parent = os.path.dirname(local_abs)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(local_abs, "wb") as f:
            f.write(bytes(raw))

    return os.path.abspath(root)
