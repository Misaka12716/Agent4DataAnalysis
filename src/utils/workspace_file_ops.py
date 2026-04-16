# utils/workspace_file_ops.py
# 工作区文件操作封装：接收相对路径，内部经工作区管理器转为绝对路径后执行。
# 供 Coder 等 Agent 作为 Tool/Function 使用；严禁路径逃逸。

from typing import Optional, List
import os
from utils.workspace_manager import to_absolute_path, resolve_workspace_root, is_safe_relative_path


def read_file(session_id: str, relative_path: str) -> Optional[str]:
    """
    在工作区内读取文件内容（相对路径）。
    路径不安全或文件不存在时返回 None。
    """
    abs_path = to_absolute_path(session_id, relative_path)
    if not abs_path or not os.path.isfile(abs_path):
        return None
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def write_file(
    session_id: str, relative_path: str, content: str, overwrite: bool = True
) -> bool:
    """
    在工作区内写入文件（相对路径）。必要时创建父目录。
    路径不安全或写入失败返回 False。
    """
    abs_path = to_absolute_path(session_id, relative_path)
    if not abs_path:
        return False
    try:
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception:
        return False


def path_exists(session_id: str, relative_path: str) -> bool:
    """判断工作区内某相对路径是否存在（文件或目录）。"""
    abs_path = to_absolute_path(session_id, relative_path)
    return abs_path is not None and os.path.exists(abs_path)


def list_dir(session_id: str, relative_path: str = ".") -> List[str]:
    """
    列出工作区内某目录下的子项名称（相对路径）。
    非目录或路径不安全时返回空列表。
    """
    abs_path = to_absolute_path(session_id, relative_path)
    if not abs_path or not os.path.isdir(abs_path):
        return []
    try:
        return os.listdir(abs_path)
    except Exception:
        return []


def create_python_file(
    session_id: str, relative_path: str, content: str, overwrite: bool = True
) -> bool:
    """
    在工作区内创建或覆盖 Python 文件。等价于 write_file，语义上强调“新建代码文件”。
    """
    if not relative_path.endswith(".py"):
        relative_path = relative_path.rstrip("/") + ".py"
    return write_file(session_id, relative_path, content, overwrite=overwrite)
