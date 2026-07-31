# -*- coding: utf-8 -*-
"""N5 — lane-local refine decision (LLM + dataset-profile driven).

Given the latest :class:`VerifyResult` for one hypothesis lane (plus the
lane's history), decide whether to **refine** the hypothesis and re-verify,
**converge** (accept the finding), or **giveup** (escalate to supervisor).

This is the *scientific-mismatch* branch (V8 §3.1): execution errors are NOT
handled here (they go straight to the supervisor); this stage only reasons
about whether a result that *ran* warrants another, sharper attempt.

Why LLM-driven (V8 §addition)
-----------------------------
The original rule-based revisions appended literal placeholder words
(``"subgroup"``, ``"confounder"``) to ``hypothesis.variables``.  Those
placeholders are not real column names, so downstream verify either ignored
them (no behaviour change → infinite-loop risk handled only by the hard
``max_iter`` cap) or treated them as analytical no-ops.  The user
correctly pointed out this is fake-refinement.

The new path:
1. Run the four-quadrant decision tree (significant?  effect strong?  CI
   wide?) to label *why* refining is warranted and what *direction* the
   refinement should take.
2. Hand that label, the latest verify numbers, and a **profile of the
   actual dataset** (column names + dtypes + basic stats) to the LLM and
   ask for a concrete revised hypothesis that uses **column names that
   exist in the profile**.
3. If the LLM is unavailable / out of quota / returns no usable
   revision → return ``giveup`` with an honest reason, rather than
   fabricating a placeholder revision.

Decision rules (all thresholds are module constants)
----------------------------------------------------
Significance is ``p < SIGNIFICANCE_ALPHA``.  A "strong" effect has
``abs(effect) >= WEAK_EFFECT_THRESHOLD``.

1. **significant + weak effect**  → ``refine``: ask LLM for a subgroup-
   probe revision that names a real moderator column from the profile.
2. **not significant + wide CI**  → ``refine``: ask LLM for a covariate /
   alternative-outcome revision that names real columns from the profile.
3. **not significant + narrow CI** → ``refine``: ask LLM for an
   angle-change revision (alternative outcome / different model framing).
4. **significant + strong effect** → ``converge``: accept the finding.

Bounded loop / stability → ``giveup``
- ``len(history) >= max_iter``                                → giveup.
- the last two history results have *converged* (effect changed by
  ``< CONVERGENCE_EFFECT_CHANGE`` relative AND the significance verdict is
  unchanged) → giveup (stable, no improvement from refining further).
- LLM unavailable / refusal at a refine branch                → giveup
  (with a clear reason; no placeholder revision is invented).

The ``decide`` signature is **backwards-compatible**: ``df``, ``public_bb``
and ``profile_text`` are new optional keyword arguments.  Callers that
omit them get the rule-based bounded loop + LLM call with an empty
profile (the LLM gets verify numbers but no schema, which usually means
it will refuse and we ``giveup`` — that is the intended honest
behaviour).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from operator_agent.hypothesis import Hypothesis
from operator_agent.discovery.blackboard import PublicBlackboard
from operator_agent.discovery.types import RefineDecision, VerifyResult

__all__ = [
    "decide",
    "SIGNIFICANCE_ALPHA",
    "WEAK_EFFECT_THRESHOLD",
    "WIDE_CI_WIDTH",
    "CONVERGENCE_EFFECT_CHANGE",
]

_LOG = logging.getLogger(__name__)

#: p-value threshold for statistical significance.
SIGNIFICANCE_ALPHA = 0.05
#: |effect| at/above this is "strong"; below is "weak".
WEAK_EFFECT_THRESHOLD = 0.2
#: A confidence interval whose width (high-low) is at/above this — or which
#: straddles zero — is treated as "wide" (imprecise).
WIDE_CI_WIDTH = 1.0
#: Relative effect change below this between two runs counts as "converged".
CONVERGENCE_EFFECT_CHANGE = 0.05
#: Cap on profile text fed to the LLM (line count).  The full profile is on
#: the public blackboard for the audit trail; the LLM only needs an excerpt.
_PROFILE_LINE_BUDGET = 120


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------
def _is_significant(v: VerifyResult) -> bool:
    return v is not None and v.p is not None and v.p < SIGNIFICANCE_ALPHA


def _is_strong(v: VerifyResult) -> bool:
    return v is not None and v.effect is not None \
        and abs(v.effect) >= WEAK_EFFECT_THRESHOLD


def _ci_is_wide(v: VerifyResult) -> bool:
    """Wide = no CI at all, straddles zero, or width >= WIDE_CI_WIDTH."""
    if v is None or v.ci is None or len(v.ci) != 2:
        return True  # absence of a CI is itself imprecise
    lo, hi = v.ci[0], v.ci[1]
    if lo is None or hi is None:
        return True
    if lo <= 0.0 <= hi:
        return True
    return (hi - lo) >= WIDE_CI_WIDTH


def _converged(a: VerifyResult, b: VerifyResult) -> bool:
    """Two results have converged: effect barely moved AND the
    significance verdict is unchanged."""
    if a is None or b is None:
        return False
    if a.effect is None or b.effect is None:
        return False
    denom = max(abs(a.effect), 1e-9)
    rel_change = abs(a.effect - b.effect) / denom
    if rel_change >= CONVERGENCE_EFFECT_CHANGE:
        return False
    return _is_significant(a) == _is_significant(b)


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------
def _profile_from_blackboard(public_bb: Optional[PublicBlackboard]
                              ) -> Optional[str]:
    """Pull the profile_text the data_processing stage already cached on the
    public blackboard, so we never recompute it per refine iteration."""
    if public_bb is None:
        return None
    try:
        entry = public_bb.get("profile")
    except Exception:
        return None
    if not isinstance(entry, dict):
        return None
    value = entry.get("value") if "value" in entry else entry
    if not isinstance(value, dict):
        return None
    text = value.get("profile_text")
    if isinstance(text, str) and text.strip():
        return text
    return None


def _profile_from_df(df: Optional[pd.DataFrame]) -> Optional[str]:
    """Fallback: compute a brief profile from the dataframe on demand."""
    if df is None:
        return None
    try:
        from operator_library.profiler import profile_df, profile_to_text
        return profile_to_text(profile_df(df), max_lines=_PROFILE_LINE_BUDGET)
    except Exception as exc:
        _LOG.debug("refine: profile_from_df failed: %r", exc)
        return None


def _resolve_profile_text(
    *,
    profile_text: Optional[str],
    public_bb: Optional[PublicBlackboard],
    df: Optional[pd.DataFrame],
) -> Optional[str]:
    """Pick the freshest profile_text we can get without recomputing twice."""
    if profile_text and profile_text.strip():
        return profile_text
    cached = _profile_from_blackboard(public_bb)
    if cached:
        return cached
    return _profile_from_df(df)


def _trim_profile(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= _PROFILE_LINE_BUDGET:
        return text
    head = lines[: _PROFILE_LINE_BUDGET - 2]
    return "\n".join(head + [f"... ({len(lines) - len(head)} more lines)"])


# ---------------------------------------------------------------------------
# LLM-driven refine
# ---------------------------------------------------------------------------
def _llm_chat_json(llm: Any, *, system: str, user: str,
                   stage: str, max_tokens: int = 800,
                   temperature: float = 0.0) -> Optional[Dict[str, Any]]:
    """Best-effort JSON chat against an injected LLM client.  Returns
    ``None`` on any failure (so callers can take the honest-giveup path)."""
    if llm is None:
        return None
    try:
        available = getattr(llm, "is_available", None)
        if available is not None and not available():
            return None
        chat_json = getattr(llm, "chat_json", None)
        if not callable(chat_json):
            return None
        out = chat_json(system=system, user=user,
                        max_tokens=max_tokens, temperature=temperature,
                        stage=stage)
    except Exception as exc:
        _LOG.info("refine: LLM call failed (%s): %r", stage, exc)
        return None
    return out if isinstance(out, dict) else None


def _build_branch_hint(verify_result: VerifyResult,
                       *, significant: bool, strong: bool) -> Dict[str, str]:
    """Translate the four-quadrant verdict into a structured hint the LLM
    can act on without re-deriving the diagnosis itself."""
    if significant and not strong:
        label = "significant_weak_effect"
        guidance = (
            "The current hypothesis produced a statistically significant "
            "but quantitatively small effect.  Propose ONE refinement that "
            "probes for a stronger effect in a more specific slice of the "
            "data — e.g. condition on a biologically/clinically meaningful "
            "column, replace one variable with a more targeted proxy, or "
            "switch to a tighter outcome that the profile supports.")
    elif _ci_is_wide(verify_result):
        label = "not_significant_wide_ci"
        guidance = (
            "The current hypothesis is not statistically significant and "
            "its confidence interval is wide / straddles zero.  Propose ONE "
            "refinement that sharpens the estimate — e.g. add a control "
            "covariate that is actually available in the profile, restrict "
            "the cohort to a subset the profile supports, or switch to a "
            "more appropriate model family for the data types you see.")
    else:
        label = "not_significant_narrow_ci"
        guidance = (
            "The current hypothesis is not statistically significant and "
            "the confidence interval is narrow — there is no detectable "
            "signal under the present framing.  Propose ONE refinement "
            "that changes the analytical angle — a different primary "
            "outcome, a different functional form (e.g. binarised vs "
            "continuous), or different exposure variables — choosing only "
            "columns that exist in the profile.")
    return {"label": label, "guidance": guidance}


_REFINE_SYSTEM_PROMPT = (
    "You are a research-hypothesis refinement agent.\n"
    "INPUT:\n"
    "  - the current hypothesis (variables + primary outcome + rationale);\n"
    "  - the latest verify numbers (effect / effect_type / p / CI / n);\n"
    "  - a structured branch hint describing WHY the current attempt "
    "is unsatisfying and what direction to refine in;\n"
    "  - a profile of the dataset (column names, dtypes, basic stats).\n"
    "TASK:\n"
    "  Propose ONE concrete revised hypothesis that follows the branch "
    "hint and is testable on this specific dataset.\n"
    "STRICT RULES:\n"
    "  1. Every name in `variables` and `primary_outcome` MUST be a column "
    "that literally appears in the provided profile.  Do not invent.\n"
    "  2. Do NOT use the literal placeholder words 'subgroup', "
    "'confounder', 'covariate', 'moderator', 'interaction' inside "
    "`variables` or `primary_outcome` — replace them with the actual "
    "column name(s) you intend.\n"
    "  3. Change MINIMALLY: alter only what the branch hint asks you "
    "to alter; carry every other field over from the current hypothesis.\n"
    "  4. If the dataset profile does not contain any column that lets you "
    "execute the requested refinement, OR every plausible refinement has "
    "already been tried in the run history, return "
    "{\"refuse\": true, \"reason\": \"...\"} — do not invent variables.\n"
    "OUTPUT: strict JSON, no markdown, with exactly these keys:\n"
    "  {\n"
    "    \"refuse\": false,\n"
    "    \"variables\": [\"col_a\", \"col_b\", ...],\n"
    "    \"primary_outcome\": \"col_z\",\n"
    "    \"edge_type\": \"associative\" | \"causal\" | \"predictive\",\n"
    "    \"rationale\": \"one-sentence justification, naming the columns\",\n"
    "    \"explanation\": \"why this refinement, one sentence\"\n"
    "  }\n"
)


def _format_verify(v: VerifyResult) -> str:
    if v is None:
        return "(no verify result)"
    return (f"effect={v.effect} ({v.effect_type}), p={v.p}, "
            f"ci={v.ci}, n={v.n}, status={v.status}")


def _format_history(history: List[VerifyResult]) -> str:
    if not history:
        return "(empty)"
    lines = []
    for i, v in enumerate(history):
        lines.append(f"  [{i}] {_format_verify(v)}")
    return "\n".join(lines)


def _format_hypothesis(h: Hypothesis) -> str:
    try:
        return json.dumps(h.to_dict(), ensure_ascii=False, default=str,
                          indent=2)
    except Exception:
        return repr(h)


def _ask_llm_to_refine(
    *,
    hypothesis: Hypothesis,
    verify_result: VerifyResult,
    history: List[VerifyResult],
    profile_text: str,
    branch_hint: Dict[str, str],
    llm: Any,
) -> Optional[Hypothesis]:
    """Call the LLM and parse a refined hypothesis.  Returns ``None`` on
    any failure / refusal (so the caller falls back to ``giveup``)."""
    user = (
        f"## Branch label\n{branch_hint['label']}\n\n"
        f"## Direction guidance\n{branch_hint['guidance']}\n\n"
        f"## Current hypothesis\n{_format_hypothesis(hypothesis)}\n\n"
        f"## Latest verify\n{_format_verify(verify_result)}\n\n"
        f"## Verify history (oldest → newest)\n"
        f"{_format_history(history)}\n\n"
        f"## Dataset profile\n```\n{_trim_profile(profile_text)}\n```\n"
    )
    out = _llm_chat_json(
        llm, system=_REFINE_SYSTEM_PROMPT, user=user,
        stage="refine_llm", max_tokens=800, temperature=0.0)
    if out is None:
        return None
    if out.get("refuse"):
        _LOG.info("refine: LLM refused to refine — reason=%s",
                  out.get("reason"))
        return None

    revised_dict = hypothesis.to_dict()

    variables = out.get("variables")
    if isinstance(variables, list):
        cleaned = [str(v).strip() for v in variables if str(v).strip()]
        if cleaned:
            revised_dict["variables"] = cleaned

    primary_outcome = out.get("primary_outcome")
    if isinstance(primary_outcome, str) and primary_outcome.strip():
        revised_dict["primary_outcome"] = primary_outcome.strip()

    edge_type = out.get("edge_type")
    if isinstance(edge_type, str) and edge_type.strip() in (
            "associative", "causal", "predictive"):
        revised_dict["edge_type"] = edge_type.strip()

    rationale_pieces: List[str] = []
    if hypothesis.rationale:
        rationale_pieces.append(str(hypothesis.rationale))
    new_rat = out.get("rationale")
    if isinstance(new_rat, str) and new_rat.strip():
        rationale_pieces.append(
            f"[refined-{branch_hint['label']}] {new_rat.strip()}")
    if rationale_pieces:
        revised_dict["rationale"] = " | ".join(rationale_pieces)

    try:
        return Hypothesis.from_dict(revised_dict)
    except Exception as exc:
        _LOG.info("refine: failed to rebuild Hypothesis from LLM output: %r",
                  exc)
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def decide(hypothesis: Hypothesis,
           verify_result: VerifyResult,
           history: List[VerifyResult],
           *,
           max_iter: int = 3,
           llm: Any = None,
           df: Optional[pd.DataFrame] = None,
           public_bb: Optional[PublicBlackboard] = None,
           profile_text: Optional[str] = None) -> RefineDecision:
    """Decide refine / converge / giveup for one lane.

    Parameters
    ----------
    hypothesis
        The current hypothesis under test (never mutated).
    verify_result
        The latest :class:`VerifyResult` to react to.
    history
        Prior (and/or current) verify results for this lane.  Used for the
        bounded-loop and stability giveup checks.
    max_iter
        Hard cap on refine iterations for this lane.
    llm
        Required injection when refining.  When the verdict warrants a
        refine but ``llm`` is unavailable, we ``giveup`` instead of
        fabricating a placeholder revision.
    df
        Optional dataframe.  When provided AND we cannot find a cached
        profile_text on the blackboard / in the caller's kwargs, we
        compute a brief profile from this df to feed the LLM.
    public_bb
        Optional public blackboard — used to read the profile text the
        data_processing stage already cached.  Prefer this over ``df``
        (no recomputation).
    profile_text
        Optional pre-built profile string.  Highest priority.
    """
    history = list(history or [])
    iteration = len(history)

    # --- bounded loop: hard cap -------------------------------------
    if iteration >= max_iter:
        return RefineDecision(
            action="giveup",
            reason=(f"reached max_iter={max_iter} "
                    f"(history has {iteration} results)"),
            iteration=iteration,
        )

    # --- stability: last two results converged ----------------------
    if len(history) >= 2 and _converged(history[-2], history[-1]):
        return RefineDecision(
            action="giveup",
            reason=("last two results converged (effect change "
                    f"< {CONVERGENCE_EFFECT_CHANGE:.0%} and significance "
                    "verdict unchanged) — refining further is unlikely to "
                    "help"),
            iteration=iteration,
        )

    significant = _is_significant(verify_result)
    strong = _is_strong(verify_result)

    # --- significant + strong → converge (no LLM needed) ------------
    if significant and strong:
        return RefineDecision(
            action="converge",
            reason=(f"significant (p={verify_result.p}) and strong effect "
                    f"(|effect|={abs(verify_result.effect):.4g} "
                    f">= {WEAK_EFFECT_THRESHOLD}) — accept the finding"),
            iteration=iteration,
        )

    # --- otherwise: hand off to LLM with a directional hint + profile
    branch_hint = _build_branch_hint(
        verify_result, significant=significant, strong=strong)
    profile_text_final = _resolve_profile_text(
        profile_text=profile_text, public_bb=public_bb, df=df)

    if not profile_text_final:
        # No profile means the LLM cannot ground variable suggestions in
        # real columns — refusing is more honest than inventing names.
        return RefineDecision(
            action="giveup",
            reason=(f"branch={branch_hint['label']}: no dataset profile "
                    "available; refusing to fabricate a placeholder "
                    "refinement"),
            iteration=iteration,
        )

    revised = _ask_llm_to_refine(
        hypothesis=hypothesis,
        verify_result=verify_result,
        history=history,
        profile_text=profile_text_final,
        branch_hint=branch_hint,
        llm=llm,
    )

    if revised is None:
        # LLM unavailable / out of quota / refused / failed to parse →
        # honest giveup (no more placeholder subgroup/confounder).
        return RefineDecision(
            action="giveup",
            reason=(f"branch={branch_hint['label']}: LLM-driven refine "
                    "produced no usable revision (LLM unavailable, refused, "
                    "or returned non-parseable output); giving up rather "
                    "than fabricating subgroup/confounder placeholders"),
            iteration=iteration,
        )

    return RefineDecision(
        action="refine",
        revised=revised,
        reason=(f"branch={branch_hint['label']}: LLM proposed a "
                "profile-grounded revision (variables drawn from the actual "
                "dataset schema)"),
        iteration=iteration,
    )
