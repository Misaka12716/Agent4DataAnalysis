import json
import os
from typing import Any, Dict, Tuple

from configs.config import READER_TEXT_PREVIEW_CHARS


def _detect_encoding(fp: str) -> str:
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"):
        try:
            with open(fp, "r", encoding=enc) as f:
                f.read(4096)
            return enc
        except (UnicodeDecodeError, OSError):
            continue
    return "utf-8"


def _read_text_preview(fp: str, max_chars: int) -> Tuple[str, str, int]:
    enc = _detect_encoding(fp)
    with open(fp, "r", encoding=enc, errors="replace") as f:
        content = f.read(max_chars + 1)
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    try:
        with open(fp, "r", encoding=enc, errors="replace") as f:
            total_lines = sum(1 for _ in f)
    except OSError:
        total_lines = line_count
    return content, enc, total_lines


def _json_summary(fp: str, encoding: str) -> Dict[str, Any]:
    """轻量 JSON 结构摘要；解析失败时返回空 dict（不影响 preview）。"""
    try:
        with open(fp, "r", encoding=encoding, errors="replace") as f:
            data = json.load(f)
    except Exception:
        return {}
    summary: Dict[str, Any] = {"json_type": type(data).__name__}
    if isinstance(data, dict):
        keys = list(data.keys())
        summary["json_keys"] = keys[:50]
        summary["json_key_count"] = len(keys)
    elif isinstance(data, list):
        summary["json_length"] = len(data)
        if data and isinstance(data[0], dict):
            summary["json_item_keys"] = list(data[0].keys())[:50]
    return summary


def digest_text_file(workspace_root: str, relative_path: str) -> Dict[str, Any]:
    fp = os.path.join(workspace_root, relative_path.replace("/", os.sep))
    ext = os.path.splitext(relative_path)[1].lower().lstrip(".")
    try:
        preview, encoding, line_count = _read_text_preview(fp, READER_TEXT_PREVIEW_CHARS)
        entry: Dict[str, Any] = {
            "file_type": "text",
            "format": ext or "unknown",
            "relative_path": relative_path,
            "encoding": encoding,
            "preview": preview,
            "line_count": line_count,
            "preview_truncated": len(preview) >= READER_TEXT_PREVIEW_CHARS,
        }
        if ext == "json":
            entry.update(_json_summary(fp, encoding))
        return entry
    except Exception as e:
        return {
            "file_type": "text",
            "format": ext or "unknown",
            "relative_path": relative_path,
            "error": str(e),
        }
