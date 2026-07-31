# utils/upload_naming.py
# 上传文件名分配：无冲突保留原名，冲突时追加 " (N)"

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Set

# 允许字母数字、下划线、连字符、点、空格、圆括号、中文；其余替换为 _
_SAFE_NAME_RE = re.compile(r"[^\w.\- ()\u4e00-\u9fff]+", re.UNICODE)
_MAX_NAME_LEN = 200


@dataclass(frozen=True)
class AllocatedUploadName:
    """客户端原名 ↔ 实际存盘/逻辑名。"""

    original_filename: str  # 客户端原始 basename（未追加冲突后缀）
    stored_name: str  # 实际落盘 / 逻辑名
    renamed: bool  # 是否因冲突追加了 (N)


def original_basename(name: str, fallback: str = "file") -> str:
    """仅剥路径，保留客户端原始 basename。"""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    return base or fallback


def safe_filename(name: str, fallback: str = "file") -> str:
    """
    清洗文件名：去路径、替换非法字符，允许空格与圆括号（供冲突后缀使用）。
    stem 与扩展名分别处理，避免非法字符导致扩展名被 strip 掉。
    """
    base = original_basename(name, fallback=fallback)
    stem, ext = os.path.splitext(base)
    cleaned_stem = _SAFE_NAME_RE.sub("_", stem)
    cleaned_stem = re.sub(r"_{2,}", "_", cleaned_stem)
    cleaned_stem = re.sub(r" {2,}", " ", cleaned_stem).strip(" ._") or fallback

    cleaned_ext = ""
    if ext:
        # 扩展名仅保留安全字符
        ext_body = _SAFE_NAME_RE.sub("", ext.lstrip(".")).strip("._")
        if ext_body:
            cleaned_ext = f".{ext_body}"

    result = f"{cleaned_stem}{cleaned_ext}"
    return result[:_MAX_NAME_LEN]


def allocate_unique_name(
    existing_names: Iterable[str],
    original_filename: str,
    *,
    fallback: str = "file",
) -> AllocatedUploadName:
    """
    在已有名称集合中分配唯一文件名。
    - 无冲突：使用清洗后的原名
    - 有冲突：stem (1).ext、stem (2).ext …
    """
    original = original_basename(original_filename, fallback=fallback)
    safe = safe_filename(original, fallback=fallback)
    existing: Set[str] = {n for n in existing_names if n}

    if safe not in existing:
        return AllocatedUploadName(
            original_filename=original,
            stored_name=safe,
            renamed=False,
        )

    stem, ext = os.path.splitext(safe)
    if not stem:
        stem = fallback
    index = 1
    while True:
        # 预留后缀长度，避免超长
        suffix = f" ({index})"
        max_stem = _MAX_NAME_LEN - len(suffix) - len(ext)
        trimmed_stem = stem[: max(1, max_stem)]
        candidate = f"{trimmed_stem}{suffix}{ext}"
        if candidate not in existing:
            return AllocatedUploadName(
                original_filename=original,
                stored_name=candidate,
                renamed=True,
            )
        index += 1


def allocate_unique_name_in_dir(
    directory: str,
    original_filename: str,
    *,
    fallback: str = "file",
    files_only: bool = True,
) -> AllocatedUploadName:
    """扫描目录已有项后分配唯一文件名。"""
    existing: Set[str] = set()
    try:
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if files_only and not os.path.isfile(path):
                continue
            existing.add(name)
    except OSError:
        pass
    return allocate_unique_name(existing, original_filename, fallback=fallback)
