"""格式注册表（2.1.2）。"""

from reader.registry.models import DEEP_PARSE_CATEGORIES, FormatRule, FormatHandler, FileCategory
from reader.registry.registry import FormatRegistry, get_format_registry, reset_format_registry_for_tests

__all__ = [
    "DEEP_PARSE_CATEGORIES",
    "FileCategory",
    "FormatRule",
    "FormatHandler",
    "FormatRegistry",
    "get_format_registry",
    "reset_format_registry_for_tests",
]
