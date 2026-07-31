# backend/resource_preview_service.py
# 文件在线预览：表格 / 图片 / PDF / NIfTI

from __future__ import annotations

import base64
import io
import os
from typing import Any, Dict, Optional, Tuple, Union

from configs.config import RESOURCES_PREVIEW_ROWS
from backend.resource_classify import _split_ext


PreviewResult = Dict[str, Any]


def _read_table(path: str, max_rows: int) -> Tuple[Optional[dict], Optional[str]]:
    try:
        import pandas as pd
    except Exception as exc:
        return None, f"pandas 不可用: {exc}"

    ext = _split_ext(path)
    try:
        if ext == ".csv":
            df = pd.read_csv(path, nrows=max_rows)
            full = pd.read_csv(path)
        elif ext == ".tsv":
            df = pd.read_csv(path, sep="\t", nrows=max_rows)
            full = pd.read_csv(path, sep="\t")
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(path, nrows=max_rows)
            full = pd.read_excel(path)
        elif ext == ".parquet":
            full = pd.read_parquet(path)
            df = full.head(max_rows)
        else:
            return None, f"不支持的表格格式: {ext}"
    except Exception as exc:
        return None, f"读取表格失败: {exc}"

    dtypes = {str(c): str(full[c].dtype) for c in full.columns}
    preview_rows = []
    for _, row in df.iterrows():
        rec = {}
        for c in df.columns:
            val = row[c]
            if hasattr(val, "item"):
                try:
                    val = val.item()
                except Exception:
                    val = str(val)
            if isinstance(val, float) and val != val:  # NaN
                val = None
            rec[str(c)] = val if val is None or isinstance(val, (str, int, float, bool)) else str(val)
        preview_rows.append(rec)

    return {
        "kind": "table",
        "columns": [str(c) for c in full.columns],
        "dtypes": dtypes,
        "preview_rows": preview_rows,
        "row_count_sample": int(len(full)),
        "truncated": len(full) > max_rows,
    }, None


def _preview_image(path: str) -> Tuple[Optional[dict], Optional[str]]:
    try:
        with open(path, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("ascii")
        ext = _split_ext(path).lstrip(".")
        mime = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
            "bmp": "image/bmp",
        }.get(ext, "application/octet-stream")
        return {
            "kind": "image",
            "mime": mime,
            "data_base64": b64,
            "size_bytes": len(raw),
        }, None
    except Exception as exc:
        return None, f"读取图片失败: {exc}"


def _preview_pdf(path: str) -> Tuple[Optional[dict], Optional[str]]:
    """返回流式下载提示；路由层可直接 FileResponse。"""
    if not os.path.isfile(path):
        return None, "PDF 文件不存在"
    return {
        "kind": "pdf",
        "mime": "application/pdf",
        "stream": True,
        "size_bytes": os.path.getsize(path),
        "download_hint": "use FileResponse",
    }, None


def _preview_nifti(path: str) -> Tuple[Optional[dict], Optional[str]]:
    try:
        import nibabel as nib
        import numpy as np
        from PIL import Image
    except Exception as exc:
        return None, f"NIfTI 预览依赖缺失（需 nibabel/Pillow）: {exc}"

    try:
        img = nib.load(path)
        data = np.asanyarray(img.dataobj)
        shape = list(data.shape)
        affine = img.affine.tolist() if hasattr(img, "affine") else None

        # 取中间切片（优先第 3 维，否则末维）
        if data.ndim >= 3:
            mid = data.shape[2] // 2
            slice_2d = data[:, :, mid]
            if data.ndim > 3:
                slice_2d = data[:, :, mid, 0]
        elif data.ndim == 2:
            slice_2d = data
            mid = 0
        else:
            return None, f"无法预览的数组维度: {data.ndim}"

        arr = np.asarray(slice_2d, dtype=np.float64)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return None, "切片无有效数值"
        lo, hi = float(np.percentile(finite, 1)), float(np.percentile(finite, 99))
        if hi <= lo:
            hi = lo + 1.0
        norm = np.clip((arr - lo) / (hi - lo), 0, 1)
        gray = (norm * 255).astype(np.uint8)
        pil = Image.fromarray(gray)
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        return {
            "kind": "nifti",
            "mime": "image/png",
            "data_base64": b64,
            "shape": shape,
            "affine": affine,
            "slice_index": int(mid),
            "slice_axis": 2 if data.ndim >= 3 else 0,
        }, None
    except Exception as exc:
        return None, f"NIfTI 预览失败: {exc}"


def _preview_text(path: str, max_chars: int = 8000) -> Tuple[Optional[dict], Optional[str]]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(max_chars + 1)
        truncated = len(text) > max_chars
        return {
            "kind": "text",
            "content": text[:max_chars],
            "truncated": truncated,
        }, None
    except Exception as exc:
        return None, f"读取文本失败: {exc}"


def build_preview(
    storage_path: str,
    category: str,
    filename: str,
    max_rows: Optional[int] = None,
) -> Tuple[Optional[PreviewResult], Optional[str]]:
    """根据分类构建预览载荷。PDF 返回 kind=pdf 并由路由决定是否 FileResponse。"""
    if not storage_path or not os.path.isfile(storage_path):
        return None, "文件不存在"

    rows = max_rows if max_rows is not None else RESOURCES_PREVIEW_ROWS
    ext = _split_ext(filename or storage_path)

    if category == "table" or ext in (".csv", ".tsv", ".xlsx", ".xls", ".parquet"):
        return _read_table(storage_path, rows)
    if category == "image" or ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return _preview_image(storage_path)
    if category == "document" or ext == ".pdf":
        if ext == ".pdf":
            return _preview_pdf(storage_path)
    if category == "imaging" or ext in (".nii", ".nii.gz"):
        if ext in (".nii", ".nii.gz"):
            return _preview_nifti(storage_path)
    if category == "text" or ext in (".txt", ".md", ".json", ".yaml", ".yml", ".log", ".xml", ".html", ".htm"):
        return _preview_text(storage_path)

    return {
        "kind": "binary",
        "message": "该格式暂不支持在线预览，请下载查看",
        "category": category,
        "extension": ext,
    }, None
