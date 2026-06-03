"""上传扩展名白名单与 Reader 分类一致性测试。"""

import os
import tempfile

import pandas as pd
import pytest

from reader.file_types import (
    all_supported_extensions,
    classify_file,
    extension_to_category,
    guess_upload_mime,
    is_upload_allowed,
    upload_allowed_extensions,
)
from reader.handlers.table import _read_raw_table


def test_upload_allowed_extensions_includes_multimodal_types():
    exts = set(upload_allowed_extensions())
    assert "png" in exts
    assert "tsv" in exts
    assert "yaml" in exts
    assert "webp" in exts
    assert "pdf" not in exts


def test_all_supported_extensions_matches_union():
    allowed = {f".{e}" for e in upload_allowed_extensions()}
    assert allowed == set(all_supported_extensions())


def test_classify_file_image_and_table():
    assert classify_file("data.png") == "image"
    assert classify_file("data.tsv") == "table"
    assert classify_file("readme.pdf") == "binary"


def test_extension_to_category():
    assert extension_to_category(".jpg") == "image"
    assert extension_to_category("csv") == "table"
    assert extension_to_category(".pdf") is None


def test_is_upload_allowed():
    assert is_upload_allowed("chart.png") is True
    assert is_upload_allowed("doc.pdf") is False


def test_guess_upload_mime():
    assert guess_upload_mime("a.webp") == "image/webp"
    assert guess_upload_mime("b.tsv") == "text/tab-separated-values"
    assert guess_upload_mime("c.yaml") == "application/x-yaml"
    assert guess_upload_mime("d.bin") == "application/octet-stream"
    assert guess_upload_mime("e.png", declared="image/custom") == "image/custom"


def test_read_raw_table_tsv_uses_tab_separator():
    content = "a\tb\n1\t2\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tsv", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = f.name
    try:
        df = _read_raw_table(path, ".tsv")
        assert df.shape == (2, 2)
        assert df.iloc[0, 0] == "a"
        assert df.iloc[1, 1] == "2"
    finally:
        os.unlink(path)


def test_read_raw_table_csv_uses_comma_separator():
    content = "a,b\n1,2\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = f.name
    try:
        df = _read_raw_table(path, ".csv")
        assert df.iloc[1, 1] == "2"
    finally:
        os.unlink(path)
