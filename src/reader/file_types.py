"""文件类型分类与上传白名单（委托 FormatRegistry）。"""

from typing import Literal, Optional

from reader.registry import get_format_registry

FileType = Literal["table", "image", "text", "document", "imaging", "binary"]

# 保留常量供外部只读参考；权威来源为 FormatRegistry 内置规则
TABLE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
TEXT_EXTENSIONS = {".txt", ".md", ".json", ".yaml", ".yml", ".log", ".xml", ".html", ".htm"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx"}
IMAGING_EXTENSIONS = {".dcm", ".dicom"}


def normalize_extension(filename: str) -> str:
    """返回小写扩展名（含点），无扩展名时返回空字符串。"""
    import os

    return os.path.splitext(filename or "")[1].lower()


def all_supported_extensions() -> frozenset[str]:
    """Reader 可深度解析的扩展名。"""
    return get_format_registry().all_supported_extensions()


def upload_allowed_extensions() -> list[str]:
    """供 Streamlit file_uploader 与 API 白名单使用的扩展名（无点、小写、排序）。"""
    return get_format_registry().upload_allowed_extensions()


def extension_to_category(ext: str) -> Optional[FileType]:
    """根据扩展名返回 Reader 类别；不在白名单内返回 None。"""
    e = ext if ext.startswith(".") else f".{ext.lower()}"
    e = e.lower()
    rule = get_format_registry().resolve(f"file{e}")
    if rule and rule.category != "binary":
        return rule.category  # type: ignore[return-value]
    return None


def is_upload_allowed(filename: str) -> bool:
    return get_format_registry().is_upload_allowed(filename)


def guess_upload_mime(filename: str, declared: Optional[str] = None) -> str:
    return get_format_registry().guess_upload_mime(filename, declared)


def classify_file(relative_path: str) -> FileType:
    return get_format_registry().classify_file(relative_path)  # type: ignore[return-value]
