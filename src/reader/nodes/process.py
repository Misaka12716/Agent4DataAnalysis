from typing import Any, Dict, List

from reader.file_types import FileType
from reader.handlers import (
    digest_binary_file,
    digest_image_file,
    digest_table_file,
    digest_text_file,
)


def process_files_node(state: Dict[str, Any]) -> Dict[str, Any]:
    root = state.get("workspace_root") or ""
    lang = state.get("lang") or "zh"
    inventory = state.get("file_inventory") or []
    digests: Dict[str, Any] = dict(state.get("file_digests") or {})
    errors: List[str] = list(state.get("errors") or [])

    for item in inventory:
        rel = item.get("relative_path") or ""
        if not rel or rel in digests:
            continue
        ft: FileType = item.get("file_type") or "binary"
        try:
            if ft == "table":
                digests[rel] = digest_table_file(root, rel)
            elif ft == "image":
                digests[rel] = digest_image_file(root, rel, lang=lang)
            elif ft == "text":
                digests[rel] = digest_text_file(root, rel)
            else:
                digests[rel] = digest_binary_file(root, rel)
        except Exception as e:
            errors.append(f"{rel}: {e}")
            digests[rel] = {
                "file_type": ft,
                "relative_path": rel,
                "error": str(e),
            }

    return {"file_digests": digests, "errors": errors}
