"""PDF 文档 Handler（优先 pypdf，回退 PyPDF2；均缺失时降级）。"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


def _open_pdf(fp: str):
    try:
        from pypdf import PdfReader  # type: ignore

        return PdfReader(fp), "pypdf"
    except ImportError:
        pass
    try:
        from PyPDF2 import PdfReader  # type: ignore

        return PdfReader(fp), "PyPDF2"
    except ImportError:
        return None, None


def digest_pdf_file(workspace_root: str, relative_path: str, **_kwargs: Any) -> Dict[str, Any]:
    fp = os.path.join(workspace_root, relative_path.replace("/", os.sep))
    entry: Dict[str, Any] = {
        "file_type": "document",
        "format": "pdf",
        "relative_path": relative_path,
        "handler_id": "document_pdf",
    }
    try:
        entry["file_size_bytes"] = os.path.getsize(fp)
    except OSError as e:
        entry["error"] = str(e)
        return entry

    reader, lib = _open_pdf(fp)
    if reader is None:
        entry["note"] = "未安装 pypdf/PyPDF2，仅登记元数据；可 pip install pypdf 启用文本抽取"
        return entry

    try:
        n_pages = len(reader.pages)
        meta = getattr(reader, "metadata", None)
        preview_parts = []
        for page in list(reader.pages)[:3]:
            try:
                text = (page.extract_text() or "").strip()
            except Exception:
                text = ""
            if text:
                preview_parts.append(text[:800])
            if sum(len(p) for p in preview_parts) >= 2000:
                break
        preview = "\n\n".join(preview_parts)[:2000]
        title: Optional[str] = None
        author: Optional[str] = None
        if meta is not None:
            title = getattr(meta, "title", None) or (meta.get("/Title") if hasattr(meta, "get") else None)
            author = getattr(meta, "author", None) or (meta.get("/Author") if hasattr(meta, "get") else None)
            if title is not None:
                title = str(title)
            if author is not None:
                author = str(author)
        entry.update(
            {
                "page_count": n_pages,
                "preview": preview,
                "preview_truncated": len(preview) >= 2000 or n_pages > 3,
                "title": title,
                "author": author,
                "parser": lib,
            }
        )
    except Exception as e:
        entry["error"] = str(e)
    return entry


class PdfDocumentHandler:
    handler_id = "document_pdf"

    def digest(self, workspace_root: str, relative_path: str, **kwargs: Any) -> Dict[str, Any]:
        return digest_pdf_file(workspace_root, relative_path, **kwargs)
