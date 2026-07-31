"""FormatRegistry 规则与白名单单测（PDF 解析见 test_reader_handlers）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reader.registry import FormatRule, reset_format_registry_for_tests


@pytest.fixture()
def isolated_registry(tmp_path: Path):
    rules_path = tmp_path / "format_rules.json"
    rules_path.write_text('{"rules": []}\n', encoding="utf-8")
    reg = reset_format_registry_for_tests(custom_rules_path=rules_path)
    yield reg
    reset_format_registry_for_tests()


def test_resolve_builtin_pdf(isolated_registry):
    rule = isolated_registry.resolve("report.pdf")
    assert rule is not None
    assert rule.category == "document"
    assert rule.handler_id == "document_pdf"
    assert isolated_registry.is_upload_allowed("report.pdf")


def test_custom_rule_mdx_binds_text(isolated_registry, tmp_path: Path):
    rule = isolated_registry.upsert_custom_rule(
        FormatRule(
            format_id="custom.mdx",
            extensions=[".mdx"],
            category="text",
            handler_id="text",
            priority=300,
            enabled=True,
        )
    )
    assert rule.format_id == "custom.mdx"
    assert isolated_registry.is_upload_allowed("page.mdx")
    assert isolated_registry.classify_file("page.mdx") == "text"

    # persisted
    raw = json.loads(isolated_registry.custom_rules_path().read_text(encoding="utf-8"))
    assert any(r["format_id"] == "custom.mdx" for r in raw["rules"])

    ok = isolated_registry.delete_custom_rule("custom.mdx")
    assert ok
    assert not isolated_registry.is_upload_allowed("page.mdx")


def test_cannot_delete_builtin(isolated_registry):
    with pytest.raises(ValueError):
        isolated_registry.delete_custom_rule("builtin.document.pdf")
