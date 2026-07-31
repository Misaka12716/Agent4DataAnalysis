"""格式规则与 Handler 协议（2.1.2）。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable

FileCategory = Literal["table", "image", "text", "document", "imaging", "binary"]

DEEP_PARSE_CATEGORIES = frozenset({"table", "image", "text", "document", "imaging"})


@dataclass
class FormatRule:
    format_id: str
    extensions: List[str]
    category: FileCategory
    handler_id: str
    mime_types: List[str] = field(default_factory=list)
    enabled: bool = True
    priority: int = 100
    builtin: bool = False
    magic_prefixes: List[str] = field(default_factory=list)
    description: str = ""

    def normalized_extensions(self) -> List[str]:
        out: List[str] = []
        for e in self.extensions or []:
            e = (e or "").strip().lower()
            if not e:
                continue
            if not e.startswith("."):
                e = f".{e}"
            out.append(e)
        return out

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["extensions"] = self.normalized_extensions()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, builtin: bool = False) -> "FormatRule":
        exts = data.get("extensions") or []
        mimes = data.get("mime_types") or []
        magics = data.get("magic_prefixes") or []
        return cls(
            format_id=str(data.get("format_id") or "").strip(),
            extensions=[str(x) for x in exts],
            category=str(data.get("category") or "binary"),  # type: ignore[arg-type]
            handler_id=str(data.get("handler_id") or "").strip(),
            mime_types=[str(x) for x in mimes],
            enabled=bool(data.get("enabled", True)),
            priority=int(data.get("priority") or 100),
            builtin=bool(data.get("builtin", builtin)),
            magic_prefixes=[str(x) for x in magics],
            description=str(data.get("description") or ""),
        )


@runtime_checkable
class FormatHandler(Protocol):
    handler_id: str

    def digest(
        self,
        workspace_root: str,
        relative_path: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        ...
