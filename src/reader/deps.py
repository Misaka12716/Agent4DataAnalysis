"""Reader 白名单格式所需解析库探测（缺包仅打 warning，不阻断服务）。"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# (显示名, import 探测可调用对象返回是否可用)
_REQUIRED_LIBS: Tuple[Tuple[str, str], ...] = (
    ("python-docx", "docx"),
    ("pypdf 或 PyPDF2", "pypdf_or_pypdf2"),
    ("pydicom", "pydicom"),
    ("openpyxl", "openpyxl"),
    ("xlrd", "xlrd"),
    ("Pillow", "PIL"),
)

_checked = False


def _probe(key: str) -> bool:
    if key == "pypdf_or_pypdf2":
        try:
            import pypdf  # noqa: F401

            return True
        except ImportError:
            pass
        try:
            import PyPDF2  # noqa: F401

            return True
        except ImportError:
            return False
    if key == "docx":
        try:
            import docx  # noqa: F401

            return True
        except ImportError:
            return False
    if key == "pydicom":
        try:
            import pydicom  # noqa: F401

            return True
        except ImportError:
            return False
    if key == "openpyxl":
        try:
            import openpyxl  # noqa: F401

            return True
        except ImportError:
            return False
    if key == "xlrd":
        try:
            import xlrd  # noqa: F401

            return True
        except ImportError:
            return False
    if key == "PIL":
        try:
            from PIL import Image  # noqa: F401

            return True
        except ImportError:
            return False
    return False


def check_reader_parse_deps(*, force: bool = False) -> Dict[str, bool]:
    """探测解析库是否安装；首次调用时对缺失项打 warning。返回 {显示名: 是否可用}。"""
    global _checked
    status: Dict[str, bool] = {}
    missing: List[str] = []
    for display, key in _REQUIRED_LIBS:
        ok = _probe(key)
        status[display] = ok
        if not ok:
            missing.append(display)
    if missing and (force or not _checked):
        logger.warning(
            "Reader 解析依赖缺失（对应格式将仅登记元数据）: %s；"
            "请 pip install -r requirements.txt（含 python-docx / pypdf / pydicom / xlrd 等）",
            ", ".join(missing),
        )
    _checked = True
    return status


def reset_deps_check_for_tests() -> None:
    global _checked
    _checked = False
