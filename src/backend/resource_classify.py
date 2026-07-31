# backend/resource_classify.py
# 文件智能分类：扩展名优先；可选委托 FormatRegistry

from __future__ import annotations

import os
from typing import Tuple

# 资源管理额外支持的扩展（含 NIfTI）
_EXTRA_CATEGORY = {
    ".nii": "imaging",
    ".nii.gz": "imaging",
    ".pkl": "binary",
    ".joblib": "binary",
    ".parquet": "table",
}

_EXTRA_MIME = {
    ".nii": "application/x-nifti",
    ".nii.gz": "application/x-nifti-gz",
    ".pkl": "application/octet-stream",
    ".joblib": "application/octet-stream",
    ".parquet": "application/x-parquet",
}

_TABLE_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".parquet"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_DOC_EXTS = {".pdf", ".docx"}
_TEXT_EXTS = {".txt", ".md", ".json", ".yaml", ".yml", ".log", ".xml", ".html", ".htm"}
_IMAGING_EXTS = {".dcm", ".dicom", ".nii", ".nii.gz"}
_MODEL_EXTS = {".pkl", ".joblib"}

_MIME_FALLBACK = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".dcm": "application/dicom",
    ".dicom": "application/dicom",
}


def _split_ext(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".nii.gz"):
        return ".nii.gz"
    return os.path.splitext(name)[1]


def _local_mime(ext: str) -> str:
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    return _MIME_FALLBACK.get(ext, "application/octet-stream")


def classify_resource_file(filename: str) -> Tuple[str, str]:
    """返回 (category, mime)。"""
    ext = _split_ext(filename)
    if ext in _EXTRA_CATEGORY:
        return _EXTRA_CATEGORY[ext], _local_mime(ext)

    # 可选：委托 FormatRegistry（失败则本地规则）
    try:
        from reader.file_types import extension_to_category, guess_upload_mime

        cat = extension_to_category(ext)
        mime = guess_upload_mime(filename)
        if cat:
            return cat, mime
    except Exception:
        pass

    if ext in _TABLE_EXTS:
        return "table", _local_mime(ext)
    if ext in _IMAGE_EXTS:
        return "image", _local_mime(ext)
    if ext in _DOC_EXTS:
        return "document", _local_mime(ext)
    if ext in _TEXT_EXTS:
        return "text", _local_mime(ext)
    if ext in _IMAGING_EXTS:
        return "imaging", _local_mime(ext)
    if ext in _MODEL_EXTS:
        return "binary", _local_mime(ext)
    return "other", "application/octet-stream"


def is_resource_upload_allowed(filename: str) -> bool:
    """资源空间上传白名单：本地扩展 + 可选 FormatRegistry。"""
    ext = _split_ext(filename)
    if ext in _EXTRA_CATEGORY or ext in (
        _TABLE_EXTS
        | _IMAGE_EXTS
        | _DOC_EXTS
        | _TEXT_EXTS
        | _IMAGING_EXTS
        | _MODEL_EXTS
    ):
        return True
    try:
        from reader.file_types import is_upload_allowed

        return is_upload_allowed(filename)
    except Exception:
        return bool(ext)


def is_table_file(filename: str) -> bool:
    return _split_ext(filename) in _TABLE_EXTS
