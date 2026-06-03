import os
from typing import Any, Dict, List

from reader.file_types import classify_file


def _iter_workspace_files(workspace_root: str) -> List[str]:
    """递归列举工作区内所有普通文件（相对路径，正斜杠）。"""
    found: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(workspace_root):
        for name in filenames:
            if name.startswith("."):
                continue
            abs_fp = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_fp, workspace_root).replace(os.sep, "/")
            found.append(rel)
    return sorted(found)


def scan_workspace_node(state: Dict[str, Any]) -> Dict[str, Any]:
    root = state.get("workspace_root") or ""
    errors: List[str] = list(state.get("errors") or [])
    inventory: List[Dict[str, Any]] = []

    if not root or not os.path.isdir(root):
        errors.append("工作区目录不存在或不可读")
        return {"file_inventory": inventory, "errors": errors}

    for rel in _iter_workspace_files(root):
        fp = os.path.join(root, rel.replace("/", os.sep))
        try:
            size = os.path.getsize(fp)
        except OSError as e:
            errors.append(f"{rel}: {e}")
            continue
        ft = classify_file(rel)
        inventory.append(
            {
                "relative_path": rel,
                "file_type": ft,
                "size_bytes": size,
            }
        )

    return {"file_inventory": inventory, "errors": errors}
