from typing import Any, Dict, List, TypedDict


class ReaderState(TypedDict, total=False):
    workspace_root: str
    session_id: str
    lang: str
    file_inventory: List[Dict[str, Any]]
    file_digests: Dict[str, Any]
    workspace_digest: Dict[str, Any]
    markdown_summary: str
    errors: List[str]
