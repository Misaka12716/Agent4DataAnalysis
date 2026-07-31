# -*- coding: utf-8 -*-
"""Novelty gate for the HYPOTHESIS-PROPOSING stage (V8 §N6 brought forward to N3).

Motivation
----------
A hypothesis that merely re-derives an already-published finding is a
*replication*, not a *discovery*.  Verifying it still costs operator runs +
LLM calls and pollutes the findings set.  This module judges each proposed
hypothesis against the literature **before** it opens a verification lane, so
the supervisor can drop confident replications up front and keep only
candidate-novel / incremental hypotheses.

Backend-agnostic by design
---------------------------
The judgement is produced by :func:`assess`, which resolves a backend in this
priority order:

1. ``checker`` — an injected callable ``(hypothesis) -> NoveltyAssessment |
   dict | None``.  This is the plug-in point for a *real* retriever (PubMed /
   Europe PMC / Semantic Scholar / local KG), exactly the N6 LITERATURE CHECK
   agent described in ``docs/v8_AGENT_DESIGN.md`` §N6.
2. ``llm`` + ``llm_novelty=True`` — a structured LLM-prior screen (no DB; a
   weak guess, clearly labelled ``source="llm_prior"``).
3. otherwise — a ``"unknown"`` stub (``source="stub"``) that **never** gates.

The gate (:func:`passes_gate`) is conservative: with no backend everything is
``unknown`` and nothing is dropped, so the framework degrades to its previous
behaviour.  A hypothesis is only dropped when a backend *confidently* says it
is a ``replication`` (or its novelty ``score`` falls below ``min_score``).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Optional

from operator_agent.hypothesis import Hypothesis
from .types import NoveltyAssessment, NOVELTY_VERDICTS

__all__ = [
    "assess",
    "passes_gate",
    "build_query",
    "DEFAULT_DROP_VERDICTS",
]

#: Verdicts the gate drops by default (only the *confident* already-published
#: one).  ``unknown`` / ``incremental`` / ``candidate_novel`` always pass.
DEFAULT_DROP_VERDICTS = ("replication",)


def build_query(hypothesis: Hypothesis) -> str:
    """Compose a compact, human-readable literature query from a hypothesis.

    Joins the finding family, primary outcome and variables — the fields a
    retriever would key on.  Kept deterministic (no LLM) so it is cheap and
    testable; a real retriever may of course refine it further.
    """
    if hypothesis is None:
        return ""
    bits = []
    fam = getattr(hypothesis, "finding_family", None)
    if fam:
        bits.append(str(fam).replace("_", " "))
    outcome = getattr(hypothesis, "primary_outcome", None)
    if outcome:
        bits.append(str(outcome).replace("_", " "))
    variables = getattr(hypothesis, "variables", None) or []
    if variables:
        bits.append(" ".join(str(v) for v in variables[:6]))
    rationale = getattr(hypothesis, "rationale", None)
    if rationale and not bits:
        bits.append(str(rationale))
    return "; ".join(b for b in bits if b).strip()


# ---------------------------------------------------------------------------
# LLM-prior backend (no database — a weak, clearly-labelled guess)
# ---------------------------------------------------------------------------
_LLM_SYSTEM = (
    "You are a literature-novelty screener for psychiatric / biomedical "
    "research.  You have NO database access — give only a PRIOR judgement of "
    "how likely the described finding is ALREADY PUBLISHED, based on your "
    "background knowledge.\n"
    "Reply with STRICT JSON and nothing else:\n"
    '  {"verdict": "candidate_novel|incremental|replication", '
    '"score": <0..1>, "rationale": "<=1 sentence"}\n'
    "Guidance: 'replication' = the core claim is well established and widely "
    "published; 'incremental' = related work exists but this specific angle "
    "is partly new; 'candidate_novel' = you are not aware of this specific "
    "finding.  score = your estimated probability it is NOVEL (1 = certainly "
    "novel, 0 = certainly already published)."
)


def _assess_with_llm(hypothesis: Hypothesis, llm: Any,
                     query: str) -> Optional[NoveltyAssessment]:
    """Structured LLM-prior novelty screen.  Returns None on any failure."""
    try:
        available = getattr(llm, "is_available", None)
        if available is not None and not available():
            return None
        chat_json = getattr(llm, "chat_json", None)
        if not callable(chat_json):
            return None
        out = chat_json(
            _LLM_SYSTEM,
            f"query: {query}\nhypothesis: "
            f"{json.dumps(hypothesis.to_dict(), ensure_ascii=False)}",
            max_tokens=160,
            temperature=0.0,
            stage="hypothesis_novelty",
        ) or {}
    except Exception:
        return None

    verdict = str(out.get("verdict", "")).strip().lower()
    if verdict not in NOVELTY_VERDICTS or verdict == "unknown":
        return None
    score = out.get("score")
    try:
        score = float(score)
    except (TypeError, ValueError):
        # fall back to a verdict-implied score
        score = {"candidate_novel": 0.8, "incremental": 0.5,
                 "replication": 0.15}.get(verdict, 0.5)
    score = max(0.0, min(1.0, score))
    rationale = str(out.get("rationale", "")).strip()
    return NoveltyAssessment(
        verdict=verdict, score=score, query=query,
        references=[], rationale=rationale or "LLM prior (no database)",
        source="llm_prior",
    )


def _coerce(result: Any, query: str) -> Optional[NoveltyAssessment]:
    """Coerce a checker's return value into a NoveltyAssessment."""
    if result is None:
        return None
    if isinstance(result, NoveltyAssessment):
        if not result.query:
            result.query = query
        return result
    if isinstance(result, dict):
        a = NoveltyAssessment.from_dict(result)
        if not a.query:
            a.query = query
        return a
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def assess(
    hypothesis: Hypothesis,
    *,
    checker: Optional[Callable[[Hypothesis], Any]] = None,
    llm: Any = None,
    llm_novelty: bool = False,
) -> NoveltyAssessment:
    """Judge one hypothesis's novelty.

    Resolution order: injected ``checker`` → optional ``llm`` prior (only when
    ``llm_novelty=True``) → ``"unknown"`` stub.  Never raises.

    Parameters
    ----------
    hypothesis
        The proposed hypothesis card.
    checker
        Real literature backend, ``(hypothesis) -> NoveltyAssessment | dict |
        None``.  The N6 retriever plugs in here.
    llm
        Optional object with ``is_available()`` + ``chat_json(...)`` for the
        LLM-prior fallback.
    llm_novelty
        Whether to use the LLM-prior fallback when ``checker`` is absent.
        Default ``False`` so a normal run adds no extra LLM calls / quota use.
    """
    query = build_query(hypothesis)

    if checker is not None:
        try:
            coerced = _coerce(checker(hypothesis), query)
        except Exception as exc:  # backend failure → unknown, never fatal
            return NoveltyAssessment(
                verdict="unknown", score=0.5, query=query,
                rationale=f"novelty checker failed: {exc!r}", source="stub")
        if coerced is not None:
            return coerced
        # checker returned nothing usable → fall through to stub/llm

    if llm_novelty and llm is not None:
        a = _assess_with_llm(hypothesis, llm, query)
        if a is not None:
            return a

    return NoveltyAssessment(
        verdict="unknown", score=0.5, query=query,
        rationale="not assessed (no literature backend)", source="stub")


def passes_gate(
    assessment: NoveltyAssessment,
    *,
    min_score: float = 0.0,
    drop_verdicts: Iterable[str] = DEFAULT_DROP_VERDICTS,
) -> bool:
    """Return True if a hypothesis should KEEP its verification lane.

    A hypothesis is dropped (gate returns False) iff EITHER:
      - its ``verdict`` is in ``drop_verdicts`` (default: a confident
        ``"replication"``), OR
      - its ``score`` is below ``min_score`` (default ``0.0`` → never drops
        on score alone).

    ``unknown`` is never in the default drop set, so a run with no literature
    backend keeps every hypothesis (backward compatible).
    """
    drop = set(drop_verdicts or ())
    if assessment.verdict in drop:
        return False
    try:
        if float(assessment.score) < float(min_score):
            return False
    except (TypeError, ValueError):
        pass
    return True
