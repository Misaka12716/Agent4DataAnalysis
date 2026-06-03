import os
from typing import Any, Dict, List, Tuple

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


def digest_text_file(workspace_root: str, relative_path: str) -> Dict[str, Any]:
    fp = os.path.join(workspace_root, relative_path.replace("/", os.sep))
    ext = os.path.splitext(relative_path)[1].lower().lstrip(".")
    try:
        preview, encoding, line_count = _read_text_preview(fp, READER_TEXT_PREVIEW_CHARS)
        return {
            "file_type": "text",
            "format": ext or "unknown",
            "relative_path": relative_path,
            "encoding": encoding,
            "preview": preview,
            "line_count": line_count,
            "preview_truncated": len(preview) >= READER_TEXT_PREVIEW_CHARS,
        }
    except Exception as e:
        return {
            "file_type": "text",
            "format": ext or "unknown",
            "relative_path": relative_path,
            "error": str(e),
        }
