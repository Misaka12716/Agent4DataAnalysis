import os
from typing import Literal, Optional

FileType = Literal["table", "image", "text", "binary"]

TABLE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
TEXT_EXTENSIONS = {".txt", ".md", ".json", ".yaml", ".yml", ".log", ".xml", ".html", ".htm"}

_UPLOAD_MIME_MAP = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".xml": "application/xml",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def normalize_extension(filename: str) -> str:
    """返回小写扩展名（含点），无扩展名时返回空字符串。"""
    return os.path.splitext(filename or "")[1].lower()


def all_supported_extensions() -> frozenset[str]:
    """Reader 可深度解析的扩展名（table + image + text）。"""
    return frozenset(TABLE_EXTENSIONS | IMAGE_EXTENSIONS | TEXT_EXTENSIONS)


def upload_allowed_extensions() -> list[str]:
    """供 Streamlit file_uploader 与 API 白名单使用的扩展名（无点、小写、排序）。"""
    return sorted(ext.lstrip(".") for ext in all_supported_extensions())


def extension_to_category(ext: str) -> Optional[FileType]:
    """根据扩展名返回 Reader 类别；不在白名单内返回 None。"""
    e = ext if ext.startswith(".") else f".{ext.lower()}"
    e = e.lower()
    if e in TABLE_EXTENSIONS:
        return "table"
    if e in IMAGE_EXTENSIONS:
        return "image"
    if e in TEXT_EXTENSIONS:
        return "text"
    return None


def is_upload_allowed(filename: str) -> bool:
    return normalize_extension(filename) in all_supported_extensions()


def guess_upload_mime(filename: str, declared: Optional[str] = None) -> str:
    if declared:
        return declared
    ext = normalize_extension(filename)
    return _UPLOAD_MIME_MAP.get(ext, "application/octet-stream")


def classify_file(relative_path: str) -> FileType:
    ext = normalize_extension(relative_path)
    if ext in TABLE_EXTENSIONS:
        return "table"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "binary"
