# -*- coding: utf-8 -*-
"""Entry router — split research-discovery intent from everything else.

``route(task)`` returns a :class:`RouteDecision`:

- ``route="discovery"`` → the task is a "can we publish / find a
  significant result / test a hypothesis" research intent → run the new
  discovery framework.
- ``route="general"``   → anything else (plotting / cleaning / generic
  analysis / Q&A) → **delegate back to the legacy flow untouched**.  The
  router never mutates or imports the legacy pipeline; ``general`` is just a
  signal for the caller to keep doing what it did before.

Two-tier decision (V8 §7 P0):
1. Keyword fast-path (cheap, deterministic, offline).
2. Lightweight LLM fallback via :mod:`operator_pipeline.llm_client` *only*
   when the keywords are inconclusive **and** an LLM is configured.  When
   no LLM is available we stay keyword-only and degrade gracefully.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["RouteDecision", "route"]


# Strong research-intent markers (Chinese + English).  Any hit → discovery.
_DISCOVERY_KEYWORDS: Tuple[str, ...] = (
    # Chinese
    "发论文", "能不能发", "能否发", "可以发", "发表", "论文", "投稿",
    "科研发现", "研究发现", "发现", "显著", "显著性", "假设", "验证假设",
    "统计显著", "效应量", "新颖", "创新点", "可发表", "p值",
    # English
    "novel", "novelty", "publish", "publishable", "publication",
    "significant", "significance", "hypothesis", "hypotheses",
    "finding", "findings", "p-value", "p value", "effect size",
    "research question", "scientific discovery",
)

# Markers that pull *toward* general (plotting / cleaning / generic ops).
_GENERAL_KEYWORDS: Tuple[str, ...] = (
    "画图", "绘图", "热力图", "柱状图", "折线图", "散点图", "可视化",
    "清洗", "去重", "缺失值", "格式转换", "导出", "画一张", "画一个",
    "plot", "heatmap", "bar chart", "line chart", "scatter", "visualize",
    "visualise", "clean the data", "dedupe", "export", "convert",
)


@dataclasses.dataclass
class RouteDecision:
    """Routing verdict.  ``route`` ∈ {"discovery", "general"}."""
    route: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"route": self.route, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RouteDecision":
        return cls(route=d.get("route", "general"),
                   reason=d.get("reason", ""))


def _matched(task_lower: str, keywords: Tuple[str, ...]) -> List[str]:
    return [kw for kw in keywords if kw.lower() in task_lower]


_LLM_SYSTEM = (
    "You are an intent router for a data-analysis assistant. Decide whether "
    "the user's task is a SCIENTIFIC DISCOVERY intent (they want to find a "
    "publishable / statistically significant finding, test a hypothesis, or "
    "ask whether results are good enough to publish) versus a GENERAL task "
    "(plotting, cleaning, format conversion, generic analysis, Q&A). "
    'Reply with JSON only: {"route": "discovery"|"general", "reason": "..."}'
)


def _llm_route(task: str, llm: Any) -> Optional[RouteDecision]:
    """Try the LLM fallback.  Returns None if unavailable or on any error."""
    try:
        if not llm.is_available():
            return None
        out = llm.chat_json(
            system=_LLM_SYSTEM,
            user=task,
            max_tokens=200,
            temperature=0.0,
            stage="router",
        )
    except Exception:
        return None
    if not isinstance(out, dict):
        return None
    r = str(out.get("route", "")).strip().lower()
    reason = str(out.get("reason", "")).strip() or "LLM intent classifier"
    if r not in ("discovery", "general"):
        return None
    return RouteDecision(route=r, reason=f"llm: {reason}")


def route(task: str, llm: Any = None) -> RouteDecision:
    """Classify ``task`` into ``discovery`` or ``general``.

    ``llm`` is an optional injection point (an object exposing
    ``is_available()`` + ``chat_json(...)``); defaults to
    :mod:`operator_pipeline.llm_client`.  The LLM is consulted **only** when
    keywords are inconclusive and it is available.
    """
    text = (task or "").strip()
    if not text:
        return RouteDecision(route="general",
                             reason="empty task → general (legacy flow)")

    low = text.lower()
    disc_hits = _matched(low, _DISCOVERY_KEYWORDS)
    gen_hits = _matched(low, _GENERAL_KEYWORDS)

    # Fast-path: a discovery marker with no competing general marker.
    if disc_hits and not gen_hits:
        return RouteDecision(
            route="discovery",
            reason=f"keyword fast-path: matched {disc_hits[:5]}")

    # Fast-path: only general markers → general.
    if gen_hits and not disc_hits:
        return RouteDecision(
            route="general",
            reason=f"keyword fast-path: matched {gen_hits[:5]}")

    # Inconclusive (both or neither) → LLM fallback, else keyword tiebreak.
    if llm is None:
        try:
            from operator_pipeline import llm_client as llm  # type: ignore
        except Exception:
            llm = None

    if llm is not None:
        decision = _llm_route(text, llm)
        if decision is not None:
            return decision

    # Keyword-only degradation.
    if disc_hits and gen_hits:
        return RouteDecision(
            route="discovery",
            reason=(f"mixed keywords (disc={disc_hits[:3]}, "
                    f"gen={gen_hits[:3]}); LLM unavailable → "
                    "defaulting to discovery"))
    return RouteDecision(
        route="general",
        reason="no research-intent keywords; LLM unavailable → general")
