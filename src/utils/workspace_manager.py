# utils/workspace_manager.py
# 工作区管理器：会话工作区生命周期、路径抽象与安全校验

import os
import uuid
from typing import Optional

# 工作区根目录（与 config 中 TEMP 或专用目录对齐）
from utils.config import TEMP_FOLDER

WORKSPACES_ROOT = os.path.join(TEMP_FOLDER, "workspaces")
INPUT_SUBDIR = "input"
OUTPUT_SUBDIR = "output"
CODE_SUBDIR = "code"

# 禁止相对路径逃逸
FORBIDDEN_PREFIXES = ("../", "..\\")


def _ensure_workspaces_root() -> str:
    os.makedirs(WORKSPACES_ROOT, exist_ok=True)
    return WORKSPACES_ROOT


def resolve_workspace_root(session_id: str) -> Optional[str]:
    """
    根据 session_id 得到该会话工作区的绝对路径（不创建目录）。
    若未初始化过则返回 None。
    """
    root = _ensure_workspaces_root()
    path = os.path.join(root, session_id)
    return path if os.path.isdir(path) else None


def init_workspace(session_id: Optional[str] = None) -> str:
    """
    为会话创建唯一工作区目录。
    - 若传入 session_id 则使用该 ID 作为目录名（用于已有会话绑定）；
    - 否则生成新的 UUID 作为 session_id 并创建目录。
    目录结构: {WORKSPACES_ROOT}/{session_id}/input/, output/, code/
    返回: 工作区绝对路径
    """
    _ensure_workspaces_root()
    if not session_id:
        session_id = str(uuid.uuid4())
    abs_path = os.path.join(WORKSPACES_ROOT, session_id)
    os.makedirs(abs_path, exist_ok=True)
    for sub in (INPUT_SUBDIR, OUTPUT_SUBDIR, CODE_SUBDIR):
        os.makedirs(os.path.join(abs_path, sub), exist_ok=True)
    return os.path.abspath(abs_path)


def get_input_dir(session_id: str) -> Optional[str]:
    """返回该会话 input 目录的绝对路径；工作区不存在则返回 None。"""
    root = resolve_workspace_root(session_id)
    if not root:
        return None
    return os.path.join(root, INPUT_SUBDIR)


def is_safe_relative_path(relative_path: str) -> bool:
    """
    校验相对路径是否安全（禁止 ../ 等逃逸出工作区）。
    只允许在工作区内的相对路径。
    """
    if not relative_path or not isinstance(relative_path, str):
        return False
    normalized = os.path.normpath(relative_path)
    if normalized.startswith(".."):
        return False
    for prefix in FORBIDDEN_PREFIXES:
        if relative_path.replace("\\", "/").startswith(prefix):
            return False
    return True


def to_absolute_path(session_id: str, relative_path: str) -> Optional[str]:
    """
    将 Agent 使用的相对路径转换为该会话工作区下的绝对路径。
    若 relative_path 不安全或工作区不存在，返回 None。
    """
    if not is_safe_relative_path(relative_path):
        return None
    root = resolve_workspace_root(session_id)
    if not root:
        return None
    return os.path.normpath(os.path.join(root, relative_path))


def get_workspace_session_id_from_abs_path(absolute_path: str) -> Optional[str]:
    """
    从绝对路径反推 session_id（若路径在 WORKSPACES_ROOT 下）。
    用于日志或校验，非必须。
    """
    root = os.path.abspath(WORKSPACES_ROOT)
    path = os.path.abspath(absolute_path)
    if not path.startswith(root):
        return None
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    return parts[0] if parts else None
