# -*- coding: utf-8 -*-
"""Stage 2 HYPOTHESIZE agent (V7 §3.3).

This is a *separate* agent from the Stage 3 VERIFY operator selector
(:mod:`operator_agent.planner`).  Each agent has its own LLM call and
its own prompt:

- Stage 2 (this module) sees ONLY: task + dataframe profile +
  hypothesis-card schema rule + finding-family few-shot examples.
  It does NOT see the operator catalog, presentation-fidelity rule,
  coder fallback, or any operator-selection logic.  Its sole output
  is a structured V8 hypothesis card (or ``None`` for generic data
  tasks).

- Stage 3 (in ``operator_agent.planner``) sees ONLY: task + dataframe
  profile + (optional) hypothesis context from Stage 2 + operator
  catalog + operator-selection rules.  It does NOT see the
  hypothesis-card schema or the finding-family few-shot examples.

The two agents are chained inside
:func:`operator_agent.planner.plan_pipeline` (2 LLM calls), but both
can also be invoked independently for tests or alternative
orchestration.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd

from operator_pipeline import llm_client
from operator_library.profiler import profile_df, profile_to_text

from operator_agent.hypothesis import Hypothesis, try_parse_hypothesis
from operator_agent.prompts import (
    build_hypothesis_agent_system_prompt,
    format_hypothesis_agent_user_message,
)


# Composed once at import time.  Built ONLY from Stage-2 blocks; the
# Stage-3 operator-selector blocks are not present in this prompt.
HYPOTHESIS_AGENT_SYSTEM: str = build_hypothesis_agent_system_prompt(lang="en")


# Reference JSON shape shown to the LLM in the user message.  Crucially,
# this example does NOT contain a ``steps`` field — that would mislead
# the Stage 2 agent into thinking it should pick operators.
_EXAMPLE_HYPOTHESIS_JSON: Dict[str, Any] = {
    "rationale": "1 short sentence describing the finding under study",
    "hypothesis": {
        "finding_family": "psychotherapy_comparison",
        "expected_hops": 2,
        "expected_agent_workflow_length": 14,
        "expected_modality": ["clinical_scale",
                                "psychotherapy_intervention"],
        "primary_outcome": "hamd_improvement",
        "cohort_id": "C02_mdd",
        "variables": ["treatment_arm", "hamd_change_12wk"],
        "edge_type": "causal",
        "rationale": "CBT vs SSRI vs combination on HAMD remission",
    },
}


@dataclass
class HypothesisResult:
    """Result of a single Stage 2 HYPOTHESIZE LLM call.

    Attributes
    ----------
    hypothesis
        The structured hypothesis card, or ``None`` if (a) the task
        was a generic data task and the LLM correctly omitted a card,
        or (b) parsing failed (in which case ``warning`` is set).
    warning
        Soft warning about parsing degradation (e.g. unknown
        ``finding_family`` aliased to ``other``, 4-hop request
        rejected and clamped, etc.).  Does NOT fail the call.
    rationale
        Free-text one-line description from the LLM.
    raw
        Full raw LLM JSON response (for debugging).
    error
        Hard error (LLM unavailable, JSON parse failure).  When set,
        ``hypothesis`` will be ``None`` and ``ok`` returns ``False``.
    """

    hypothesis: Optional[Hypothesis] = None
    warning: Optional[str] = None
    rationale: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def has_hypothesis(self) -> bool:
        return self.hypothesis is not None


def propose_hypothesis(
    task: str,
    df: pd.DataFrame,
    *,
    max_tokens: int = 800,
    temperature: float = 0.0,
) -> HypothesisResult:
    """Stage 2 HYPOTHESIZE: emit a structured Hypothesis card or None.

    Parameters
    ----------
    task
        User's natural-language task description.
    df
        User's dataframe; only its profile is sent to the LLM.
    max_tokens, temperature
        LLM call knobs.  Defaults are deterministic.

    Returns
    -------
    HypothesisResult
        See class docstring.  ``HypothesisResult.hypothesis`` is
        ``None`` for generic data tasks (e.g. ``"draw a heatmap"``).
    """
    if not llm_client.is_available():
        return HypothesisResult(
            error="LLM not configured (.env missing API key/base URL)",
        )

    profile = profile_df(df)
    profile_text = profile_to_text(profile, max_lines=120)

    user_msg = format_hypothesis_agent_user_message(
        task=task.strip(),
        profile_text=profile_text,
        example=json.dumps(_EXAMPLE_HYPOTHESIS_JSON,
                            ensure_ascii=False, indent=2),
        lang="en",
    )

    try:
        raw = llm_client.chat_json(
            HYPOTHESIS_AGENT_SYSTEM, user_msg,
            max_tokens=max_tokens,
            temperature=temperature,
            stage="planner_hypothesis",
        )
    except llm_client.LLMError as e:
        return HypothesisResult(error=f"LLM call failed: {e}")

    rationale = str(raw.get("rationale", "")).strip()

    if "hypothesis" not in raw or raw.get("hypothesis") is None:
        # LLM correctly omitted hypothesis (generic data task).
        return HypothesisResult(
            hypothesis=None,
            rationale=rationale,
            raw=raw,
        )

    hyp_raw = raw["hypothesis"]
    if not isinstance(hyp_raw, dict):
        return HypothesisResult(
            hypothesis=None,
            warning=(f"hypothesis field is not a JSON object: "
                       f"{type(hyp_raw).__name__}"),
            rationale=rationale,
            raw=raw,
        )

    hyp, parse_warning = try_parse_hypothesis(hyp_raw)
    return HypothesisResult(
        hypothesis=hyp,
        warning=parse_warning,
        rationale=rationale,
        raw=raw,
    )


__all__ = [
    "HypothesisResult",
    "HYPOTHESIS_AGENT_SYSTEM",
    "propose_hypothesis",
]
