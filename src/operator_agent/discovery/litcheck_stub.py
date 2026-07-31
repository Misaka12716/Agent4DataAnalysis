# -*- coding: utf-8 -*-
"""N6 literature-context placeholder.

This phase does **no** external retrieval / knowledge-graph / literature
database (V8 §2 — out of scope).  :func:`run` always returns ``None`` so the
review stage's ``novelty`` is provisional (LLM prior only) and the compile
stage writes ``literature_context: None`` into ``findings.yaml``.

The :class:`LitContext` dataclass exists so later phases can fill it in
without changing the interface that downstream code depends on.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

__all__ = ["LitContext", "run"]


@dataclasses.dataclass
class LitContext:
    """Minimal placeholder for retrieved literature context.

    All fields default empty; populated only in a future phase.
    """
    query: str = ""
    references: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    novelty_assessment: Optional[str] = None
    source: str = "stub"          # "stub" until real retrieval lands

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "references": [dict(r) for r in self.references],
            "novelty_assessment": self.novelty_assessment,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LitContext":
        return cls(
            query=d.get("query", ""),
            references=list(d.get("references") or []),
            novelty_assessment=d.get("novelty_assessment"),
            source=d.get("source", "stub"),
        )


def run(hypothesis: Any = None, *args: Any,
        **kwargs: Any) -> Optional[LitContext]:
    """N6 stub — always returns ``None`` (no retrieval in this phase).

    Signature is intentionally permissive so later phases can add real
    parameters (hypothesis, requirement summary, top_k, …) without breaking
    callers that already pass a hypothesis positionally.
    """
    return None
