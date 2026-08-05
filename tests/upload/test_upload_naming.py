# tests/upload/test_upload_naming.py
"""上传文件名分配：无冲突保留原名，冲突时追加 (N)。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.upload_naming import (  # noqa: E402
    allocate_unique_name,
    allocate_unique_name_in_dir,
    original_basename,
    safe_filename,
)


def test_no_conflict_keeps_name():
    alloc = allocate_unique_name(set(), "report.xlsx")
    assert alloc.stored_name == "report.xlsx"
    assert alloc.original_filename == "report.xlsx"
    assert alloc.renamed is False


def test_conflict_appends_paren_index():
    existing = {"a.xlsx"}
    alloc = allocate_unique_name(existing, "a.xlsx")
    assert alloc.stored_name == "a (1).xlsx"
    assert alloc.original_filename == "a.xlsx"
    assert alloc.renamed is True

    existing2 = {"a.xlsx", "a (1).xlsx"}
    alloc2 = allocate_unique_name(existing2, "a.xlsx")
    assert alloc2.stored_name == "a (2).xlsx"


def test_conflict_when_paren_slot_taken_skips():
    existing = {"a.xlsx", "a (1).xlsx", "a (2).xlsx"}
    alloc = allocate_unique_name(existing, "a.xlsx")
    assert alloc.stored_name == "a (3).xlsx"


def test_strips_path_traversal():
    assert original_basename("../../etc/passwd.csv") == "passwd.csv"
    alloc = allocate_unique_name(set(), r"..\..\evil.xlsx")
    assert alloc.stored_name == "evil.xlsx"
    assert "/" not in alloc.stored_name
    assert "\\" not in alloc.stored_name


def test_safe_filename_allows_space_and_parens():
    assert safe_filename("我的 报告 (初稿).xlsx") == "我的 报告 (初稿).xlsx"


def test_safe_filename_replaces_illegal_chars():
    cleaned = safe_filename('bad<>:"/\\|?*.csv')
    assert "<" not in cleaned
    assert ">" not in cleaned
    assert ":" not in cleaned
    assert cleaned.endswith(".csv")


def test_allocate_in_dir(tmp_path: Path):
    (tmp_path / "demo.csv").write_text("x\n", encoding="utf-8")
    alloc = allocate_unique_name_in_dir(str(tmp_path), "demo.csv")
    assert alloc.stored_name == "demo (1).csv"
    assert alloc.renamed is True

    alloc2 = allocate_unique_name_in_dir(str(tmp_path), "other.csv")
    assert alloc2.stored_name == "other.csv"
    assert alloc2.renamed is False


def test_generate_data_filename_wrapper(tmp_path: Path):
    from utils.workspace_manager import generate_data_filename

    assert generate_data_filename(str(tmp_path), "表.xlsx") == "表.xlsx"
    (tmp_path / "表.xlsx").write_bytes(b"1")
    assert generate_data_filename(str(tmp_path), "表.xlsx") == "表 (1).xlsx"


def test_chinese_dicom_filename_preserved():
    assert safe_filename("患者CT.dcm") == "患者CT.dcm"
    alloc = allocate_unique_name(set(), "患者CT.dcm")
    assert alloc.stored_name == "患者CT.dcm"
    assert alloc.renamed is False

    alloc2 = allocate_unique_name({"患者CT.dcm"}, "患者CT.dcm")
    assert alloc2.stored_name == "患者CT (1).dcm"
    assert alloc2.original_filename == "患者CT.dcm"
    assert alloc2.renamed is True
