from typing import Any, Dict, List


def _build_summary(files: Dict[str, Any]) -> str:
    if not files:
        return "工作区未发现可解析文件"
    counts = {
        "table": 0,
        "image": 0,
        "text": 0,
        "document": 0,
        "imaging": 0,
        "binary": 0,
    }
    for info in files.values():
        ft = info.get("file_type") or "binary"
        counts[ft] = counts.get(ft, 0) + 1
    labels = [
        ("table", "个表格"),
        ("image", "个图片"),
        ("text", "个文本"),
        ("document", "个文档"),
        ("imaging", "个影像"),
        ("binary", "个其它"),
    ]
    parts = []
    for key, suffix in labels:
        n = counts.get(key) or 0
        if n:
            parts.append(f"{n} {suffix}")
    detail = "、".join(parts) if parts else "0 个文件"
    return f"共 {len(files)} 个文件：{detail}"


def merge_digest_node(state: Dict[str, Any]) -> Dict[str, Any]:
    digests = state.get("file_digests") or {}
    workspace_digest: Dict[str, Any] = {
        "files": digests,
        "summary": _build_summary(digests),
    }
    errs = state.get("errors") or []
    if errs:
        workspace_digest["reader_errors"] = errs
    return {"workspace_digest": workspace_digest}
