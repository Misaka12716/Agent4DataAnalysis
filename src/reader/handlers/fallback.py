import os
from typing import Any, Dict


def digest_binary_file(workspace_root: str, relative_path: str) -> Dict[str, Any]:
    fp = os.path.join(workspace_root, relative_path.replace("/", os.sep))
    ext = os.path.splitext(relative_path)[1].lower().lstrip(".")
    entry: Dict[str, Any] = {
        "file_type": "binary",
        "format": ext or "unknown",
        "relative_path": relative_path,
        "note": "未深度解析的二进制或其它类型文件",
    }
    try:
        entry["file_size_bytes"] = os.path.getsize(fp)
    except Exception as e:
        entry["error"] = str(e)
    return entry
