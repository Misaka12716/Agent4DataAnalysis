# utils/workspace_manager.py
# 工作区管理器：会话工作区生命周期、路径抽象与安全校验

import os
import uuid
import base64
from typing import Dict, List, Optional

# 工作区根目录（与 config 中 TEMP 或专用目录对齐）
from configs.config import TEMP_FOLDER

WORKSPACES_ROOT = os.path.join(TEMP_FOLDER, "workspaces")

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
    为会话创建唯一工作区目录（仅根目录，不创建 input/output/code 子目录）。
    - 若传入 session_id 则使用该 ID 作为目录名（用于已有会话绑定）；
    - 否则生成新的 UUID 作为 session_id 并创建目录。
    返回: 工作区绝对路径
    """
    _ensure_workspaces_root()
    if not session_id:
        session_id = str(uuid.uuid4())
    abs_path = os.path.join(WORKSPACES_ROOT, session_id)
    os.makedirs(abs_path, exist_ok=True)
    return os.path.abspath(abs_path)


def generate_data_filename(workspace_abs: str, original_filename: str) -> str:
    """
    根据工作区已有文件，生成统一的数据文件名：
    - 第一个文件：data.扩展名（如 data.xlsx）
    - 后续文件：data_1.扩展名、data_2.扩展名，依此类推

    仅基于文件扩展名进行区分，文件名部分统一为 data / data_N。
    """
    # 提取原始扩展名（含点），若无扩展名则空字符串
    _, ext = os.path.splitext(original_filename or "")
    # 规范化扩展名为小写
    ext = ext.lower()

    # 已存在的同扩展名文件名集合，便于快速判断
    try:
        existing = {
            name
            for name in os.listdir(workspace_abs)
            if os.path.isfile(os.path.join(workspace_abs, name))
        }
    except OSError:
        existing = set()

    # 优先使用 data.ext
    base_name = f"data{ext}"
    if base_name not in existing:
        return base_name

    # 否则从 data_1.ext 开始递增查找空位
    index = 1
    while True:
        candidate = f"data_{index}{ext}"
        if candidate not in existing:
            return candidate
        index += 1


def list_workspace_files(session_id: str) -> list:
    """
    列出工作区根目录下所有文件的相对路径（不含子目录）。
    工作区不存在或非目录时返回空列表。
    """
    root = resolve_workspace_root(session_id)
    if not root:
        return []
    try:
        return [
            name for name in os.listdir(root)
            if os.path.isfile(os.path.join(root, name))
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
    用于日志或校验，非必须。
    """
    root = os.path.abspath(WORKSPACES_ROOT)
    path = os.path.abspath(absolute_path)
    if not path.startswith(root):
        return None
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    return parts[0] if parts else None


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
