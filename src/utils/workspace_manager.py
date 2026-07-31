# utils/workspace_manager.py
# 工作区管理器：会话工作区生命周期、路径抽象与安全校验

import os
import uuid
import base64
from typing import Dict, List, Optional

# 工作区根目录（与 config 中 TEMP 或专用目录对齐）
from configs.config import TEMP_FOLDER

WORKSPACES_ROOT = os.path.join(TEMP_FOLDER, "workspaces")

PROJECT_SUBDIRS = ("raw", "processed", "outputs", "archive", "sessions")

# 禁止相对路径逃逸
FORBIDDEN_PREFIXES = ("../", "..\\")


def _ensure_workspaces_root() -> str:
    os.makedirs(WORKSPACES_ROOT, exist_ok=True)
    return WORKSPACES_ROOT


def project_root_for(user_id: int, project_id: int) -> str:
    """计算用户级隔离下的项目工作区绝对路径（不创建目录）。"""
    return os.path.abspath(
        os.path.join(WORKSPACES_ROOT, str(int(user_id)), str(int(project_id)))
    )


def init_project_workspace(user_id: int, project_id: int) -> str:
    """为项目创建唯一工作区目录及子目录 raw/processed/outputs/archive/sessions。"""
    if user_id <= 0:
        raise ValueError("user_id 必须为正整数")
    if project_id <= 0:
        raise ValueError("project_id 必须为正整数")
    _ensure_workspaces_root()
    abs_path = project_root_for(user_id, project_id)
    os.makedirs(abs_path, exist_ok=True)
    for sub in PROJECT_SUBDIRS:
        os.makedirs(os.path.join(abs_path, sub), exist_ok=True)
    return abs_path


def init_session_in_project(user_id: int, project_id: int, session_id: Optional[str] = None) -> str:
    """在项目 sessions/ 下创建会话工作区目录。"""
    if user_id <= 0:
        raise ValueError("user_id 必须为正整数")
    if project_id <= 0:
        raise ValueError("project_id 必须为正整数")
    _ensure_workspaces_root()
    sid = (session_id or "").strip() or str(uuid.uuid4())
    project_root = project_root_for(user_id, project_id)
    for sub in PROJECT_SUBDIRS:
        os.makedirs(os.path.join(project_root, sub), exist_ok=True)
    session_path = os.path.join(project_root, "sessions", sid)
    os.makedirs(session_path, exist_ok=True)
    return os.path.abspath(session_path)


def resolve_project_root(project_id: int) -> Optional[str]:
    """根据 project_id 得到项目工作区绝对路径（不创建目录）。"""
    if project_id <= 0:
        return None
    from db.project_store import ProjectStore

    row, err = ProjectStore.get_project(project_id)
    if not err and row:
        db_path = str(row.get("workspace_abs_path") or "").strip()
        if db_path:
            abs_db = os.path.abspath(db_path)
            if os.path.isdir(abs_db):
                return abs_db
        user_id = int(row.get("user_id") or 0)
        if user_id > 0:
            path = project_root_for(user_id, project_id)
            if os.path.isdir(path):
                return path
    return None


def workspace_path_for(user_id: int, session_id: str) -> str:
    """计算用户级隔离下的会话工作区绝对路径（不创建目录）。"""
    return os.path.abspath(
        os.path.join(WORKSPACES_ROOT, str(int(user_id)), (session_id or "").strip())
    )


def resolve_workspace_root(session_id: str) -> Optional[str]:
    """
    根据 session_id 得到该会话工作区的绝对路径（不创建目录）。
    解析顺序：DB workspace_abs_path → user_id/session_id 布局。
    若未初始化过则返回 None。
    """
    sid = (session_id or "").strip()
    if not sid:
        return None

    from db.session_store import SessionStore

    db_path = SessionStore.get_workspace_path(sid)
    if db_path:
        abs_db = os.path.abspath(str(db_path).strip())
        if os.path.isdir(abs_db):
            return abs_db

    row, err = SessionStore.get_session_user(sid)
    if not err and row and row.get("user_id"):
        path = workspace_path_for(int(row["user_id"]), sid)
        if os.path.isdir(path):
            return path

    return None


def init_workspace(
    user_id: int,
    session_id: Optional[str] = None,
    project_id: Optional[int] = None,
) -> str:
    """
    为会话创建唯一工作区目录。
    - 有 project_id：workspaces/<user_id>/<project_id>/sessions/<session_id>/
    - 无 project_id（旧逻辑）：workspaces/<user_id>/<session_id>/
    """
    if project_id and int(project_id) > 0:
        sid = (session_id or "").strip() or str(uuid.uuid4())
        return init_session_in_project(user_id, int(project_id), sid)
    if user_id <= 0:
        raise ValueError("user_id 必须为正整数")
    _ensure_workspaces_root()
    sid = (session_id or "").strip() or str(uuid.uuid4())
    abs_path = workspace_path_for(user_id, sid)
    os.makedirs(abs_path, exist_ok=True)
    return abs_path


def generate_data_filename(workspace_abs: str, original_filename: str) -> str:
    """
    为上传文件分配工作区文件名（薄包装，兼容旧调用点）。

    - 无同名冲突：保留清洗后的用户原名
    - 有冲突：原名 (1).ext / 原名 (2).ext …
    """
    from utils.upload_naming import allocate_unique_name_in_dir

    return allocate_unique_name_in_dir(workspace_abs, original_filename).stored_name


def list_workspace_files(session_id: str) -> list:
    """
    列出工作区根目录下所有文件的相对路径（不含子目录）。
    工作区不存在或非目录时返回空列表。
    跳过 SESSION_MEMORY.md（由编排维护，勿注入 Coder/Reader 上下文以免自嵌套）。
    """
    root = resolve_workspace_root(session_id)
    if not root:
        return []
    try:
        return [
            name for name in os.listdir(root)
            if os.path.isfile(os.path.join(root, name))
            and name != "SESSION_MEMORY.md"
        ]
    except OSError:
        return []


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
    支持 layouts:
    - workspaces/<user_id>/<session_id>/...
    - workspaces/<user_id>/<project_id>/sessions/<session_id>/...
    """
    root = os.path.abspath(WORKSPACES_ROOT)
    path = os.path.abspath(absolute_path)
    if not path.startswith(root + os.sep) and path != root:
        return None
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    if len(parts) >= 4 and parts[2] == "sessions":
        return parts[3]
    if len(parts) >= 2:
        return parts[1]
    return None


def build_workspace_tree(workspace_abs: str) -> Dict[str, object]:
    """
    构建工作区目录树，返回目录与文件的层级结构。
    """
    root = os.path.abspath(workspace_abs)

    def _walk(abs_path: str, rel_path: str) -> Dict[str, object]:
        node_name = os.path.basename(abs_path) if rel_path else ""
        if os.path.isdir(abs_path):
            children: List[Dict[str, object]] = []
            try:
                entries = sorted(os.listdir(abs_path))
            except OSError:
                entries = []
            for entry in entries:
                child_abs = os.path.join(abs_path, entry)
                child_rel = os.path.join(rel_path, entry) if rel_path else entry
                children.append(_walk(child_abs, child_rel))
            return {
                "name": node_name,
                "type": "directory",
                "relative_path": rel_path,
                "children": children,
            }

        file_size = 0
        try:
            file_size = int(os.path.getsize(abs_path))
        except OSError:
            file_size = 0
        return {
            "name": node_name,
            "type": "file",
            "relative_path": rel_path,
            "size": file_size,
        }

    return _walk(root, "")


def build_workspace_files_payload(workspace_abs: str) -> List[Dict[str, object]]:
    """
    返回工作区内全部实际文件数据（包含内容）：
    - UTF-8 可解码文件：encoding=text，content 为文本内容
    - 非 UTF-8 文件：encoding=base64，content 为 base64 文本
    """
    root = os.path.abspath(workspace_abs)
    files: List[Dict[str, object]] = []

    for current_root, dir_names, file_names in os.walk(root):
        dir_names.sort()
        file_names.sort()
        for file_name in file_names:
            abs_path = os.path.join(current_root, file_name)
            rel_path = os.path.relpath(abs_path, root)
            rel_path = rel_path.replace(os.sep, "/")

            try:
                with open(abs_path, "rb") as f:
                    raw = f.read()
                size = int(len(raw))
            except OSError:
                continue

            try:
                content = raw.decode("utf-8")
                encoding = "text"
            except UnicodeDecodeError:
                content = base64.b64encode(raw).decode("ascii")
                encoding = "base64"

            files.append(
                {
                    "name": file_name,
                    "relative_path": rel_path,
                    "size": size,
                    "encoding": encoding,
                    "content": content,
                }
            )

    return files
