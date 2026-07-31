from typing import Any, Dict, List

from reader.registry import get_format_registry


def process_files_node(state: Dict[str, Any]) -> Dict[str, Any]:
    root = state.get("workspace_root") or ""
    lang = state.get("lang") or "zh"
    inventory = state.get("file_inventory") or []
    digests: Dict[str, Any] = dict(state.get("file_digests") or {})
    errors: List[str] = list(state.get("errors") or [])
    registry = get_format_registry()

    for item in inventory:
        rel = item.get("relative_path") or ""
        if not rel or rel in digests:
            continue
        ft = item.get("file_type") or "binary"
        try:
            digests[rel] = registry.dispatch_digest(
                root, rel, lang=lang, file_type=ft
            )
        except Exception as e:
            errors.append(f"{rel}: {e}")
            digests[rel] = {
                "file_type": ft,
                "relative_path": rel,
                "error": str(e),
            }

    return {"file_digests": digests, "errors": errors}
