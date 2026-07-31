"""FormatRegistry：格式识别、上传白名单与 digest 分发。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from reader.registry.adapters import wrap_legacy_handlers
from reader.registry.builtin_rules import builtin_format_rules
from reader.registry.models import (
    DEEP_PARSE_CATEGORIES,
    FileCategory,
    FormatHandler,
    FormatRule,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CUSTOM_RULES_PATH = _REPO_ROOT / "knowledge" / "format_rules.json"

_MIME_FALLBACK = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".xml": "application/xml",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
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
    ".dcm": "application/dicom",
    ".dicom": "application/dicom",
}


def _normalize_ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _hex_prefix(data: bytes, n: int = 8) -> str:
    return data[:n].hex()


class FormatRegistry:
    def __init__(self, custom_rules_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._handlers: Dict[str, FormatHandler] = {}
        self._builtin_rules: List[FormatRule] = []
        self._custom_rules: List[FormatRule] = []
        self._custom_rules_path = Path(custom_rules_path) if custom_rules_path else _DEFAULT_CUSTOM_RULES_PATH
        self._bootstrap()

    def _bootstrap(self) -> None:
        for h in wrap_legacy_handlers().values():
            self.register_handler(h)
        try:
            from reader.handlers.document_pdf import PdfDocumentHandler
            from reader.handlers.document_docx import DocxDocumentHandler
            from reader.handlers.imaging_dicom import DicomImagingHandler

            self.register_handler(PdfDocumentHandler())
            self.register_handler(DocxDocumentHandler())
            self.register_handler(DicomImagingHandler())
        except Exception:
            pass
        self._builtin_rules = list(builtin_format_rules())
        self.reload_custom_rules()

    def register_handler(self, handler: FormatHandler) -> None:
        with self._lock:
            self._handlers[handler.handler_id] = handler

    def list_handlers(self) -> List[str]:
        with self._lock:
            return sorted(self._handlers.keys())

    def custom_rules_path(self) -> Path:
        return self._custom_rules_path

    def reload_custom_rules(self) -> None:
        path = self._custom_rules_path
        rules: List[FormatRule] = []
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                items = raw.get("rules") if isinstance(raw, dict) else raw
                for item in items or []:
                    if not isinstance(item, dict):
                        continue
                    rule = FormatRule.from_dict(item, builtin=False)
                    if not rule.format_id or not rule.handler_id:
                        continue
                    rule.builtin = False
                    rules.append(rule)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                rules = []
        with self._lock:
            self._custom_rules = rules

    def _persist_custom_rules(self) -> None:
        path = self._custom_rules_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"rules": [r.to_dict() for r in self._custom_rules]}
        # strip builtin flag noise for custom file
        for item in payload["rules"]:
            item["builtin"] = False
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def list_rules(self, *, include_disabled: bool = True) -> List[FormatRule]:
        with self._lock:
            rules = list(self._builtin_rules) + list(self._custom_rules)
        if include_disabled:
            return rules
        return [r for r in rules if r.enabled]

    def get_rule(self, format_id: str) -> Optional[FormatRule]:
        fid = (format_id or "").strip()
        with self._lock:
            for r in self._custom_rules + self._builtin_rules:
                if r.format_id == fid:
                    return r
        return None

    def upsert_custom_rule(self, rule: FormatRule) -> FormatRule:
        if not rule.format_id:
            raise ValueError("format_id 必填")
        if rule.format_id.startswith("builtin."):
            raise ValueError("不可覆盖内置 format_id；请用不同 id 或 disable 内置规则")
        if rule.handler_id not in self._handlers:
            raise ValueError(f"未知 handler_id: {rule.handler_id}；可用: {', '.join(self.list_handlers())}")
        if rule.category not in DEEP_PARSE_CATEGORIES and rule.category != "binary":
            raise ValueError(f"无效 category: {rule.category}")
        rule.builtin = False
        with self._lock:
            # disable colliding builtin by priority only at resolve-time; store custom
            replaced = False
            for i, existing in enumerate(self._custom_rules):
                if existing.format_id == rule.format_id:
                    self._custom_rules[i] = rule
                    replaced = True
                    break
            if not replaced:
                self._custom_rules.append(rule)
            self._persist_custom_rules()
        return rule

    def delete_custom_rule(self, format_id: str) -> bool:
        fid = (format_id or "").strip()
        if fid.startswith("builtin."):
            raise ValueError("内置规则不可删除，仅可 disable")
        with self._lock:
            before = len(self._custom_rules)
            self._custom_rules = [r for r in self._custom_rules if r.format_id != fid]
            changed = len(self._custom_rules) != before
            if changed:
                self._persist_custom_rules()
            return changed

    def set_builtin_enabled(self, format_id: str, enabled: bool) -> FormatRule:
        fid = (format_id or "").strip()
        with self._lock:
            for r in self._builtin_rules:
                if r.format_id == fid:
                    r.enabled = bool(enabled)
                    return r
        raise ValueError(f"内置规则不存在: {fid}")

    def resolve(
        self,
        filename: str,
        mime: Optional[str] = None,
        head_bytes: Optional[bytes] = None,
    ) -> Optional[FormatRule]:
        ext = _normalize_ext(filename)
        mime_l = (mime or "").split(";")[0].strip().lower() or None
        magic = _hex_prefix(head_bytes, 8) if head_bytes else None

        candidates: List[FormatRule] = []
        with self._lock:
            all_rules = list(self._custom_rules) + list(self._builtin_rules)

        for rule in all_rules:
            if not rule.enabled:
                continue
            exts = rule.normalized_extensions()
            matched = False
            if ext and ext in exts:
                matched = True
            if not matched and mime_l and mime_l in [m.lower() for m in (rule.mime_types or [])]:
                matched = True
            if not matched and magic and rule.magic_prefixes:
                for pref in rule.magic_prefixes:
                    p = pref.lower().replace(" ", "")
                    if magic.startswith(p):
                        matched = True
                        break
            if matched:
                candidates.append(rule)

        if not candidates:
            return None
        # 自定义优先：同等 priority 时 custom 在前；再按 priority 降序
        candidates.sort(key=lambda r: (0 if not r.builtin else 1, -int(r.priority)))
        return candidates[0]

    def classify_file(self, relative_path: str) -> FileCategory:
        rule = self.resolve(relative_path)
        if rule:
            return rule.category  # type: ignore[return-value]
        return "binary"

    def is_upload_allowed(self, filename: str, mime: Optional[str] = None) -> bool:
        rule = self.resolve(filename, mime=mime)
        return rule is not None and rule.enabled and rule.category in DEEP_PARSE_CATEGORIES

    def all_supported_extensions(self) -> frozenset[str]:
        exts: set[str] = set()
        for rule in self.list_rules(include_disabled=False):
            if rule.category in DEEP_PARSE_CATEGORIES:
                exts.update(rule.normalized_extensions())
        return frozenset(exts)

    def upload_allowed_extensions(self) -> List[str]:
        return sorted(e.lstrip(".") for e in self.all_supported_extensions())

    def guess_upload_mime(self, filename: str, declared: Optional[str] = None) -> str:
        if declared:
            return declared
        ext = _normalize_ext(filename)
        if ext in _MIME_FALLBACK:
            return _MIME_FALLBACK[ext]
        rule = self.resolve(filename)
        if rule and rule.mime_types:
            return rule.mime_types[0]
        return "application/octet-stream"

    def dispatch_digest(
        self,
        workspace_root: str,
        relative_path: str,
        *,
        lang: str = "zh",
        file_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        rule = self.resolve(relative_path)
        handler_id = rule.handler_id if rule else "binary"
        category = rule.category if rule else (file_type or "binary")
        handler = self._handlers.get(handler_id) or self._handlers.get("binary")
        if handler is None:
            return {
                "file_type": category,
                "relative_path": relative_path,
                "error": f"无可用 handler: {handler_id}",
            }
        kwargs: Dict[str, Any] = {}
        if handler_id == "image":
            kwargs["lang"] = lang
        result = handler.digest(workspace_root, relative_path, **kwargs)
        if isinstance(result, dict):
            result.setdefault("file_type", category)
            result.setdefault("relative_path", relative_path)
            if rule:
                result.setdefault("format_id", rule.format_id)
                result.setdefault("handler_id", rule.handler_id)
        return result

    def catalog_entry(self, relative_path: str, size_bytes: int) -> Dict[str, Any]:
        rule = self.resolve(relative_path)
        category = rule.category if rule else "binary"
        return {
            "relative_path": relative_path,
            "file_category": category,
            "size_bytes": size_bytes,
            "format_id": rule.format_id if rule else None,
            "handler_id": rule.handler_id if rule else "binary",
            "deep_parse": bool(rule and rule.category in DEEP_PARSE_CATEGORIES),
        }


_registry: Optional[FormatRegistry] = None
_registry_lock = threading.Lock()


def get_format_registry() -> FormatRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                from reader.deps import check_reader_parse_deps

                check_reader_parse_deps()
                _registry = FormatRegistry()
    return _registry


def reset_format_registry_for_tests(custom_rules_path: Optional[Path] = None) -> FormatRegistry:
    """测试用：重建全局注册表。"""
    global _registry
    with _registry_lock:
        _registry = FormatRegistry(custom_rules_path=custom_rules_path)
        return _registry
