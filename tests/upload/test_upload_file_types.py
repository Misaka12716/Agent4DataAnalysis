"""上传扩展名白名单与 Reader 分类一致性测试。"""

import sys
from unittest.mock import MagicMock

# 避免 reader 链路拉取 langchain_openai（部分 CI/环境未安装）
sys.modules.setdefault("langchain_openai", MagicMock())

from reader.file_types import (
    all_supported_extensions,
    classify_file,
    extension_to_category,
    guess_upload_mime,
    is_upload_allowed,
    upload_allowed_extensions,
)

TABLE_EXTS = ("csv", "tsv", "xlsx", "xls")
TEXT_EXTS = ("txt", "md", "json", "yaml", "xml", "html", "log")
TEXT_MIME = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
    "log": "text/plain; charset=utf-8",
    "json": "application/json",
    "yaml": "application/x-yaml",
    "xml": "application/xml",
    "html": "text/html; charset=utf-8",
}


def test_upload_allowed_extensions_includes_multimodal_types():
    exts = set(upload_allowed_extensions())
    assert "png" in exts
    assert "yaml" in exts
    assert "webp" in exts
    assert "pdf" in exts
    assert "docx" in exts
    assert "dcm" in exts
    for ext in TABLE_EXTS:
        assert ext in exts
    for ext in TEXT_EXTS:
        assert ext in exts
    assert "yml" in exts
    assert "htm" in exts


def test_all_supported_extensions_matches_union():
    allowed = {f".{e}" for e in upload_allowed_extensions()}
    assert allowed == set(all_supported_extensions())


def test_classify_file_image_and_table():
    assert classify_file("data.png") == "image"
    for ext in TABLE_EXTS:
        assert classify_file(f"data.{ext}") == "table"
        assert is_upload_allowed(f"data.{ext}") is True
    assert classify_file("readme.pdf") == "document"
    assert classify_file("note.docx") == "document"
    assert classify_file("scan.dcm") == "imaging"


def test_classify_file_text_extensions():
    for ext in TEXT_EXTS:
        assert classify_file(f"sample.{ext}") == "text"
        assert is_upload_allowed(f"sample.{ext}") is True
    assert classify_file("config.yml") == "text"
    assert is_upload_allowed("config.yml") is True
    assert classify_file("page.htm") == "text"
    assert is_upload_allowed("page.htm") is True


def test_extension_to_category():
    assert extension_to_category(".jpg") == "image"
    for ext in TABLE_EXTS:
        assert extension_to_category(ext) == "table"
        assert extension_to_category(f".{ext}") == "table"
    for ext in TEXT_EXTS:
        assert extension_to_category(ext) == "text"
        assert extension_to_category(f".{ext}") == "text"
    assert extension_to_category(".yml") == "text"
    assert extension_to_category(".htm") == "text"
    assert extension_to_category(".pdf") == "document"
    assert extension_to_category(".bin") is None


def test_is_upload_allowed():
    assert is_upload_allowed("chart.png") is True
    assert is_upload_allowed("doc.pdf") is True
    assert is_upload_allowed("notes.txt") is True
    assert is_upload_allowed("x.bin") is False


def test_guess_upload_mime():
    assert guess_upload_mime("a.webp") == "image/webp"
    assert guess_upload_mime("b.csv") == "text/csv"
    assert guess_upload_mime("b.tsv") == "text/tab-separated-values"
    assert (
        guess_upload_mime("c.xlsx")
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert guess_upload_mime("d.xls") == "application/vnd.ms-excel"
    for ext, mime in TEXT_MIME.items():
        assert guess_upload_mime(f"sample.{ext}") == mime
    assert guess_upload_mime("c.yml") == "application/x-yaml"
    assert guess_upload_mime("d.htm") == "text/html; charset=utf-8"
    assert guess_upload_mime("d.bin") == "application/octet-stream"
    assert guess_upload_mime("e.png", declared="image/custom") == "image/custom"
    assert guess_upload_mime("f.pdf") == "application/pdf"


def test_chinese_dicom_and_table_fixture_names():
    assert is_upload_allowed("患者CT.dcm") is True
    assert classify_file("患者CT.dcm") == "imaging"
    assert "dicom" in guess_upload_mime("患者CT.dcm")
    for ext in TABLE_EXTS:
        name = f"mixed-types.{ext}"
        assert classify_file(name) == "table"
        assert is_upload_allowed(name) is True
    assert classify_file("large-dataset.csv") == "table"
    assert is_upload_allowed("large-dataset.csv") is True


def test_text_fixture_names():
    for ext in TEXT_EXTS:
        name = f"sample.{ext}"
        assert classify_file(name) == "text"
        assert is_upload_allowed(name) is True
        assert guess_upload_mime(name) == TEXT_MIME[ext]
