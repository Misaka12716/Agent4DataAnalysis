"""上传扩展名白名单与 Reader 分类一致性测试。"""

from reader.file_types import (
    all_supported_extensions,
    classify_file,
    extension_to_category,
    guess_upload_mime,
    is_upload_allowed,
    upload_allowed_extensions,
)


def test_upload_allowed_extensions_includes_multimodal_types():
    exts = set(upload_allowed_extensions())
    assert "png" in exts
    assert "tsv" in exts
    assert "yaml" in exts
    assert "webp" in exts
    assert "pdf" in exts
    assert "docx" in exts
    assert "dcm" in exts


def test_all_supported_extensions_matches_union():
    allowed = {f".{e}" for e in upload_allowed_extensions()}
    assert allowed == set(all_supported_extensions())


def test_classify_file_image_and_table():
    assert classify_file("data.png") == "image"
    assert classify_file("data.tsv") == "table"
    assert classify_file("readme.pdf") == "document"
    assert classify_file("note.docx") == "document"
    assert classify_file("scan.dcm") == "imaging"


def test_extension_to_category():
    assert extension_to_category(".jpg") == "image"
    assert extension_to_category("csv") == "table"
    assert extension_to_category(".pdf") == "document"
    assert extension_to_category(".bin") is None


def test_is_upload_allowed():
    assert is_upload_allowed("chart.png") is True
    assert is_upload_allowed("doc.pdf") is True
    assert is_upload_allowed("x.bin") is False


def test_guess_upload_mime():
    assert guess_upload_mime("a.webp") == "image/webp"
    assert guess_upload_mime("b.tsv") == "text/tab-separated-values"
    assert guess_upload_mime("c.yaml") == "application/x-yaml"
    assert guess_upload_mime("d.bin") == "application/octet-stream"
    assert guess_upload_mime("e.png", declared="image/custom") == "image/custom"
    assert guess_upload_mime("f.pdf") == "application/pdf"
