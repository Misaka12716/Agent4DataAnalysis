# -*- coding: utf-8 -*-
"""Two-call HYPOTHESIZE generator (token-lean variant) — discovery only.

Rationale (user request, 2026-05): the single-call Stage-2 prompt is large
(~3.5k tokens) because it bundles the full hypothesis-card schema *and* 13
few-shot examples in one shot.  An ideation agent fundamentally needs very
little context.  This module splits the work into two cheap calls:

  Call 1 — IDEATE
      Minimal prompt: role + exactly ONE tool-task example (A) + ONE
      finding example (B); **no card schema**.  Input is only what the
      data-flow hands this agent: the task + a compact dataframe profile.
      Output: ``{"is_finding": bool, "idea": "<1-2 sentences>"}``.

  Call 2 — SHAPE  (only fired when call 1 says ``is_finding``)
      Takes the free-text idea + profile and fits it to the V8 hypothesis
      card schema.  Reuses the shared ``hypothesis_card`` prompt block (the
      authoritative enum/schema) but NOT the 13-example few-shot, so it
      stays small.  Output is the standard
      ``{"rationale": ..., "hypothesis": {...}}`` card.

The function returns the same :class:`~operator_agent.hypothesis_agent.HypothesisResult`
shape as the one-call ``propose_hypothesis`` so it is a drop-in ``impl`` for
:func:`operator_agent.discovery.hypothesis_stage.run` (and the supervisor's
``hypothesis_impl`` injection point).  No edits to the shared ``prompts.py``
or ``hypothesis_agent.py`` are required.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from operator_pipeline import llm_client
from operator_library.profiler import profile_df, profile_to_text

from operator_agent.hypothesis import try_parse_hypothesis
from operator_agent.hypothesis_agent import (
    HypothesisResult,
    _EXAMPLE_HYPOTHESIS_JSON,
)
from operator_agent.prompts import get_prompt_block

from .data_shape_inference import format_shape_banner, infer_shape

__all__ = ["propose_hypothesis_twostep", "build_ideation_system_prompt",
           "build_shape_system_prompt"]


# --------------------------------------------------------------------------
# Call 1 — IDEATE: tiny prompt, exactly one (A) and one (B) example, no schema
# --------------------------------------------------------------------------
_IDEATION_SYSTEM = (
    "You are a scientific-finding IDEATION agent (V8 Stage-2, ideation half).\n"
    "You receive a research task and a compact profile of the user's dataset.\n"
    "Decide whether the task is a SCIENTIFIC FINDING question (does X "
    "cause / predict / moderate / mediate Y; compare arms on an outcome; "
    "estimate an effect of a treatment) or a generic DATA / TOOL task "
    "(plot, heatmap, correlation matrix, clean / fill, summary stats, "
    "reshape, filter, sort).\n"
    "If it IS a scientific finding question, propose exactly ONE concrete, "
    "testable finding idea in 1-2 sentences, naming the concrete dataset "
    "columns it would use (treatment / exposure, outcome, key covariates).\n"
    "You do NOT pick analysis methods, operators, code, or any schema "
    "fields — you ONLY describe the idea in plain language.\n"
    "Respond with STRICT JSON and nothing else:\n"
    '  {"is_finding": true|false, "idea": "<1-2 sentences naming columns, '
    'or empty string when not a finding>"}\n'
    "\n"
    "Example (A) — generic tool task:\n"
    '  Task: "Make a Pearson correlation matrix of all columns and draw a '
    'heatmap."\n'
    '  -> {"is_finding": false, "idea": ""}\n'
    "\n"
    "Example (B) — scientific finding:\n"
    '  Task: "Compare CBT vs SSRI vs combined CBT+SSRI on HAMD improvement '
    'at 12 weeks."\n'
    '  -> {"is_finding": true, "idea": "Combined CBT+SSRI yields greater '
    "12-week HAMD improvement than CBT or SSRI alone; relate treatment_arm "
    'to hamd_change_12wk, adjusting for baseline severity."}'
)

_IDEATION_USER_TEMPLATE = (
    "## Research task\n{task}\n\n"
    "## Dataset profile\n{profile_text}\n\n"
    "Return only the JSON object."
)


def build_ideation_system_prompt() -> str:
    """Return the (small) call-1 ideation system prompt."""
    return _IDEATION_SYSTEM


# --------------------------------------------------------------------------
# Call 2 — SHAPE: reuse the shared schema block, no few-shot
# --------------------------------------------------------------------------
_SHAPE_INTRO = (
    "You convert a one-line research IDEA into ONE structured V8 hypothesis "
    "card.\n"
    "The idea has ALREADY been judged a genuine scientific finding by an "
    "upstream agent; do NOT second-guess that and do NOT apply the "
    "(A) tool-task branch below — you must ALWAYS emit a `hypothesis` card "
    "here.\n"
    "Map the idea + dataset profile onto the schema below.\n"
    "For `variables`, use the scientific entities in the IDEA (biological "
    "pathways, gene sets, phenotypes, exposures, treatments, or clinical "
    "constructs). NEVER put raw dataframe column names such as `gene_symbol`, "
    "`logFC`, `p_value`, `adj_p_value`, `id`, or other bookkeeping/statistic "
    "columns in `variables`.\n"
    "`primary_outcome` may be an actual outcome column when the dataset has "
    "one; for result tables such as DEG/enrichment outputs, use an "
    "interpretable biological endpoint from the idea (for example "
    "`synaptic_signaling_enrichment`) rather than a statistic column.\n"
    "Use concrete dataframe column names only in the rationale or metadata "
    "when explaining how the idea can be tested.\n"
)

_SHAPE_USER_TEMPLATE = (
    "## Finding idea (from the ideation step)\n{idea}\n\n"
    "## Dataset profile\n{profile_text}\n\n"
    "## Output JSON shape (copy the STRUCTURE; replace values to match the "
    "idea + the actual columns)\n{example}\n\n"
    'Return only the JSON object: {{"rationale": "...", "hypothesis": '
    "{{...}}}}."
)


def build_shape_system_prompt(lang: str = "en") -> str:
    """Return the call-2 schema-fit system prompt (schema block, no few-shot)."""
    parts = [
        _SHAPE_INTRO,
        "Hard rules:",
        "  " + get_prompt_block("output_format", lang).replace("\n", "\n  "),
        "  " + get_prompt_block("hypothesis_card", lang).replace("\n", "\n  "),
    ]
    return "\n".join(parts)


# Composed once at import time (call-2 schema prompt is static).
_SHAPE_SYSTEM_EN = build_shape_system_prompt("en")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _coerce_is_finding(value: Any) -> bool:
    """Coerce a possibly-string ``is_finding`` flag to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _run_ideation(
    task: str, profile_text: str, *, max_tokens: int, temperature: float,
) -> Tuple[Optional[bool], str, Dict[str, Any], Optional[str]]:
    """Call 1.  Returns ``(is_finding, idea, raw, error)``."""
    user_msg = _IDEATION_USER_TEMPLATE.format(
        task=task.strip(), profile_text=profile_text)
    try:
        raw = llm_client.chat_json(
            _IDEATION_SYSTEM, user_msg,
            max_tokens=max_tokens, temperature=temperature,
            stage="hypothesis_ideate",
        )
    except llm_client.LLMError as exc:
        return None, "", {}, f"ideation LLM call failed: {exc}"
    idea = str(raw.get("idea", "")).strip()
    is_finding = _coerce_is_finding(raw.get("is_finding", bool(idea)))
    return is_finding, idea, raw, None


def _run_shape(
    idea: str, profile_text: str, *, max_tokens: int, temperature: float,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Call 2.  Returns ``(raw_card, error)``."""
    user_msg = _SHAPE_USER_TEMPLATE.format(
        idea=idea,
        profile_text=profile_text,
        example=json.dumps(_EXAMPLE_HYPOTHESIS_JSON, ensure_ascii=False,
                           indent=2),
    )
    try:
        raw = llm_client.chat_json(
            _SHAPE_SYSTEM_EN, user_msg,
            max_tokens=max_tokens, temperature=temperature,
            stage="hypothesis_shape",
        )
    except llm_client.LLMError as exc:
        return {}, f"shape LLM call failed: {exc}"
    return raw, None


# --------------------------------------------------------------------------
# public entry — drop-in ``impl`` for hypothesis_stage.run
# --------------------------------------------------------------------------
def propose_hypothesis_twostep(
    task: str,
    df: pd.DataFrame,
    *,
    ideate_max_tokens: int = 256,
    shape_max_tokens: int = 512,
    temperature: float = 0.0,
    seed: Optional[int] = None,  # accepted for impl-signature compatibility
) -> HypothesisResult:
    """Two-call Stage-2 HYPOTHESIZE: ideate, then fit to schema.

    Drop-in replacement for
    :func:`operator_agent.hypothesis_agent.propose_hypothesis`.

    Returns a :class:`HypothesisResult` whose ``hypothesis`` is ``None`` when
    call 1 classifies the task as a generic data/tool task (no second call is
    made in that case — saving the whole schema prompt).
    """
    if not llm_client.is_available():
        return HypothesisResult(
            error="LLM not configured (.env missing API key/base URL)")

    profile = profile_df(df)
    profile_text = profile_to_text(profile, max_lines=120)

    # Schema-aware shape banner (DEG result / RCT / case-control / survival).
    # This mirrors what the upstream N2 data_processing stage prepends so the
    # twostep generator works the same whether it is invoked through the
    # supervisor (which already feeds an annotated profile via public_bb in
    # future revisions) or directly with a bare dataframe.
    inferred = infer_shape(df)
    if inferred is not None:
        profile_text = format_shape_banner(inferred) + "\n\n" + profile_text

    # --- Call 1: ideation (tiny prompt, no schema) ----------------------
    is_finding, idea, idea_raw, err = _run_ideation(
        task, profile_text,
        max_tokens=ideate_max_tokens, temperature=temperature)
    if err is not None:
        return HypothesisResult(error=err)

    if not is_finding or not idea:
        # Generic data/tool task — correctly no hypothesis; skip call 2.
        return HypothesisResult(hypothesis=None, rationale=idea, raw=idea_raw)

    # --- Call 2: shape the idea into the V8 card schema -----------------
    card_raw, err = _run_shape(
        idea, profile_text,
        max_tokens=shape_max_tokens, temperature=temperature)
    if err is not None:
        return HypothesisResult(error=err)

    rationale = str(card_raw.get("rationale", "")).strip() or idea

    hyp_raw = card_raw.get("hypothesis")
    if hyp_raw is None:
        # Shape step declined to emit a card despite a finding idea — surface
        # as a soft warning, not a hard error (the lane simply opens nothing).
        return HypothesisResult(
            hypothesis=None,
            warning="shape step returned no hypothesis card for a finding idea",
            rationale=rationale,
            raw={"ideation": idea_raw, "shape": card_raw},
        )
    if not isinstance(hyp_raw, dict):
        return HypothesisResult(
            hypothesis=None,
            warning=(f"hypothesis field is not a JSON object: "
                     f"{type(hyp_raw).__name__}"),
            rationale=rationale,
            raw={"ideation": idea_raw, "shape": card_raw},
        )

    hyp, parse_warning = try_parse_hypothesis(hyp_raw)
    return HypothesisResult(
        hypothesis=hyp,
        warning=parse_warning,
        rationale=rationale,
        raw={"ideation": idea_raw, "shape": card_raw},
    )
