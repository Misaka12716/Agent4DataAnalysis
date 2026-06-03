from typing import Any, Dict, List


def _build_summary(files: Dict[str, Any]) -> str:
    if not files:
        return "工作区未发现可解析文件"
    counts = {"table": 0, "image": 0, "text": 0, "binary": 0}
    for info in files.values():
        ft = info.get("file_type") or "binary"
        counts[ft] = counts.get(ft, 0) + 1
    parts = []
    if counts.get("table"):
        parts.append(f"{counts['table']} 个表格")
    if counts.get("image"):
        parts.append(f"{counts['image']} 个图片")
    if counts.get("text"):
        parts.append(f"{counts['text']} 个文本")
    if counts.get("binary"):
        parts.append(f"{counts['binary']} 个其它")
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
