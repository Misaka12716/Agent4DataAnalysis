# utils/workspace_file_ops.py
# 工作区文件操作封装：经 runtime 统一读写（本地或可选沙箱后端）。

from typing import Optional, List

from runtime.factory import ensure_runtime
from utils.workspace_manager import is_safe_relative_path


def read_file(session_id: str, relative_path: str) -> Optional[str]:
    """
    在工作区内读取文件内容（相对路径）。
    路径不安全或文件不存在时返回 None。
    """
    if not is_safe_relative_path(relative_path):
        return None
    rt = ensure_runtime(session_id)
    data = rt.files.read(relative_path, format="text")
    return data if isinstance(data, str) else None


def write_file(
    session_id: str, relative_path: str, content: str, overwrite: bool = True
) -> bool:
    """
    在工作区内写入文件（相对路径）。必要时创建父目录。
    路径不安全或写入失败返回 False。
    """
    if not is_safe_relative_path(relative_path):
        return False
    if not overwrite and ensure_runtime(session_id).files.exists(relative_path):
        return False
    return ensure_runtime(session_id).files.write(relative_path, content or "") is not None


def path_exists(session_id: str, relative_path: str) -> bool:
    """判断工作区内某相对路径是否存在（文件或目录）。"""
    if not is_safe_relative_path(relative_path):
        return False
    return ensure_runtime(session_id).files.exists(relative_path)


def list_dir(session_id: str, relative_path: str = ".") -> List[str]:
    """
    列出工作区内某目录下的子项名称（相对路径）。
    非目录或路径不安全时返回空列表。
    """
    rt = ensure_runtime(session_id)
    prefix = "" if relative_path in (".", "./", "") else relative_path.strip("/") + "/"
    names: set[str] = set()
    for rel in rt.files.list(relative_path, depth=1):
        rest = rel[len(prefix) :] if prefix and rel.startswith(prefix) else rel
        if not rest:
            continue
        names.add(rest.split("/")[0])
    return sorted(names)


def create_python_file(
    session_id: str, relative_path: str, content: str, overwrite: bool = True
) -> bool:
    """
    在工作区内创建或覆盖 Python 文件。等价于 write_file，语义上强调“新建代码文件”。
    """
    if not relative_path.endswith(".py"):
        relative_path = relative_path.rstrip("/") + ".py"
    return write_file(session_id, relative_path, content, overwrite=overwrite)


def write_bytes_file(session_id: str, relative_path: str, data: bytes) -> bool:
    """写入二进制文件（上传场景）。"""
    if not is_safe_relative_path(relative_path):
        return False
    return ensure_runtime(session_id).files.write(relative_path, data) is not None


def delete_file(session_id: str, relative_path: str) -> bool:
    """删除工作区内的普通文件（相对路径）；路径不安全或不存在时返回 False。"""
    if not is_safe_relative_path(relative_path):
        return False
    return bool(ensure_runtime(session_id).files.delete(relative_path))
