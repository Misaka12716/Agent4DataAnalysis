# -*- coding: utf-8 -*-
"""Review agent (评审) — **numbers-only credibility** (V8 §11).

The reviewer's job is to decide whether a verified finding is credible
*using only the reproducible numbers* produced by the verify stage
(``effect`` / ``ci`` / ``p`` / ``n`` / ``seed`` / ``dataset_hash`` /
``operator_versions`` / ``artifact_paths``).  It never trusts prose: every
flag is derived from a number, and every entry in
:attr:`ReviewResult.reasons` cites the actual number (and, where relevant,
the artifact paths the number came from) so the verdict is one-click
traceable back to a concrete operator run.

Four checks (V8 §5 / §11)
-------------------------
1. ``stat_validity``      — the run actually produced usable, finite
                            statistics on enough data.
2. ``effect_meaningful``  — the effect clears a minimal-important-difference
                            (MID) threshold chosen by ``effect_type``.
3. ``multiplicity_ok``    — multiple tests were either not run or were
                            corrected (heuristic over ``verify.extra``).
4. ``novelty_provisional``— a *provisional* note only.  With no literature
                            DB this is ``"provisional: not assessed ..."``;
                            statistical credibility never depends on it.

``verdict`` is ``"pass"`` iff the first three (hard, number-based) flags are
all True; ``novelty`` is informational and does **not** gate the verdict.

The function degrades gracefully: it never needs a live LLM (novelty uses an
optional injected ``llm``; the default works without one) and never raises on
malformed numbers — a bad number simply fails its check.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from operator_agent.hypothesis import Hypothesis
from .types import ReviewResult, VerifyResult
from .blackboard import PrivateBlackboard
from .signals import SignalBus, SignalType

__all__ = [
    "run",
    "DEFAULT_MID_THRESHOLDS",
    "DEFAULT_MID",
    "RATIO_EFFECT_TYPES",
    "MIN_N",
]

# ---------------------------------------------------------------------------
# Constants — documented thresholds (V8 §11)
# ---------------------------------------------------------------------------
#: Minimum sample size for a statistically valid run.  Below this the run is
#: treated as under-powered regardless of p/CI.
MIN_N: int = 20

#: Minimal-important-difference (MID) thresholds keyed by (lower-cased)
#: ``effect_type``.  The rule is ``|effect| >= MID`` for *additive* effect
#: measures (beta / standardized mean difference / Cohen's d / correlation /
#: ATE), and a *fold-change from the null of 1.0* for *ratio* measures
#: (odds-ratio / risk-ratio / hazard-ratio) — see :data:`RATIO_EFFECT_TYPES`.
#: Defaults follow common convention: standardized/beta ~0.2 (small effect,
#: Cohen), correlation ~0.1, OR/RR/HR ~1.5 (a 50% change in odds/risk), and a
#: small ATE default of 0.1.  Override per-call via ``mid_thresholds``.
DEFAULT_MID_THRESHOLDS: Dict[str, float] = {
    "beta": 0.2,
    "standardized": 0.2,
    "standardized_beta": 0.2,
    "smd": 0.2,
    "cohens_d": 0.2,
    "d": 0.2,
    "g": 0.2,
    "hedges_g": 0.2,
    "correlation": 0.1,
    "r": 0.1,
    "pearson_r": 0.1,
    "ate": 0.1,
    "att": 0.1,
    "mean_diff": 0.1,
    "or": 1.5,
    "odds_ratio": 1.5,
    "rr": 1.5,
    "risk_ratio": 1.5,
    "relative_risk": 1.5,
    "hr": 1.5,
    "hazard_ratio": 1.5,
}

#: Fallback MID when the ``effect_type`` is unknown / missing (the "ATE small
#: default" of the contract).
DEFAULT_MID: float = 0.1

#: Effect types interpreted as *ratios* (null value = 1.0); meaningfulness is
#: judged by the fold-change ``max(or, 1/or)`` rather than ``|effect|``.
RATIO_EFFECT_TYPES = frozenset({
    "or", "odds_ratio", "rr", "risk_ratio", "relative_risk",
    "hr", "hazard_ratio",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_finite_number(v: Any) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _ci_pair(ci: Any) -> Optional[List[float]]:
    """Return ``[lo, hi]`` as finite floats, or None if not a valid pair."""
    if ci is None:
        return None
    try:
        lo, hi = ci
    except (TypeError, ValueError):
        return None
    if not (_is_finite_number(lo) and _is_finite_number(hi)):
        return None
    return [float(lo), float(hi)]


def _mid_for(effect_type: Optional[str],
             mid_thresholds: Optional[Dict[str, float]]) -> float:
    et = (effect_type or "").strip().lower()
    table = dict(DEFAULT_MID_THRESHOLDS)
    if mid_thresholds:
        table.update({str(k).strip().lower(): float(v)
                      for k, v in mid_thresholds.items()})
    return table.get(et, DEFAULT_MID)


def _novelty_note(hypothesis: Optional[Hypothesis], llm: Any) -> str:
    """Provisional novelty note.  Uses ``llm`` only if available; never
    raises; always returns a string prefixed ``"provisional:"``.
    """
    default = "provisional: not assessed (no literature DB)"
    if llm is None:
        return default
    try:
        available = getattr(llm, "is_available", None)
        if available is not None and not available():
            return default
        chat_json = getattr(llm, "chat_json", None)
        if not callable(chat_json):
            return default
        fam = getattr(hypothesis, "finding_family", None) if hypothesis else None
        out = chat_json(
            system=("You are a literature-novelty screener. With no database, "
                    "give a one-line PRIOR guess of novelty. "
                    'Reply JSON {"novelty": "..."} (<=1 sentence).'),
            user=f"finding_family={fam}; "
                 f"hypothesis={hypothesis.to_dict() if hypothesis else None}",
            max_tokens=120,
            stage="review_novelty",
        )
        note = (out or {}).get("novelty") or (out or {}).get("summary")
        if isinstance(note, str) and note.strip():
            s = note.strip()
            return s if s.lower().startswith("provisional:") \
                else f"provisional: {s}"
    except Exception:
        pass
    return default


def _check_multiplicity(verify: VerifyResult) -> (bool, str):  # type: ignore
    """Heuristic over ``verify.extra``.

    Rule:
      - ``extra["multiplicity_corrected"] is True``  → OK (correction applied).
      - else if a test count (``n_tests`` / ``num_tests`` / ``n_comparisons``
        / ``n_outcomes``) is present and ``> 1`` → NOT OK (multiple tests
        without correction).
      - otherwise (single test, or no multiplicity info) → OK.
    """
    extra = verify.extra or {}
    corrected = extra.get("multiplicity_corrected")
    if corrected is True:
        return True, "multiplicity correction applied (multiplicity_corrected=True)"
    n_tests: Optional[int] = None
    for k in ("n_tests", "num_tests", "n_comparisons", "n_outcomes"):
        if extra.get(k) is not None:
            try:
                n_tests = int(extra[k])
                break
            except (TypeError, ValueError):
                continue
    if n_tests is not None and n_tests > 1:
        return False, (f"{n_tests} tests run without correction "
                       f"(multiplicity_corrected not set)")
    if n_tests == 1:
        return True, "single test (n_tests=1; no multiple-comparison risk)"
    return True, ("single test / no multiplicity flag "
                  "(multiplicity_corrected not set)")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run(hypothesis: Hypothesis,
        verify_result: VerifyResult,
        *,
        private_bb: Optional[PrivateBlackboard] = None,
        llm: Any = None,
        bus: Optional[SignalBus] = None,
        mid_thresholds: Optional[Dict[str, float]] = None) -> ReviewResult:
    """Review one verified hypothesis using only its reproducible numbers.

    Parameters
    ----------
    hypothesis
        The hypothesis card under review (used only for the novelty note and
        provenance; the verdict is driven entirely by ``verify_result``).
    verify_result
        The :class:`VerifyResult` carrying the §11 number-credibility
        contract.
    private_bb
        When given, the result is written under key ``"review_result"`` with
        producer ``"review_stage"``.
    llm
        Optional object exposing ``is_available()`` + ``chat_json(...)`` for a
        provisional novelty note.  Absent/unavailable → graceful default.
    bus
        Optional :class:`SignalBus`; a :attr:`SignalType.Done` signal is
        emitted on completion.
    mid_thresholds
        Optional per-call override of :data:`DEFAULT_MID_THRESHOLDS`.

    Returns
    -------
    ReviewResult
        With ``reasons`` that cite the actual numbers + artifact paths.
    """
    reasons: List[str] = []

    # ---------------- 1) statistical validity ----------------
    status_ok = (verify_result.status == "ok")
    p = verify_result.p
    p_ok = (p is not None) and _is_finite_number(p)
    ci = _ci_pair(verify_result.ci)
    ci_ok = ci is not None
    n = verify_result.n
    n_ok = (n is not None) and _is_finite_number(n) and int(n) >= MIN_N

    stat_validity = bool(status_ok and p_ok and ci_ok and n_ok)

    # status
    reasons.append(f"status={verify_result.status}"
                   + ("" if status_ok else " (not 'ok')"))
    # p
    if p_ok:
        reasons.append(f"p={p} {'<' if float(p) < 0.05 else '>='} 0.05")
    elif p is None:
        reasons.append("p=None (missing → stat_validity fails)")
    else:
        reasons.append(f"p={p} is NaN/non-finite (stat_validity fails)")
    # ci
    if ci_ok:
        lo, hi = ci  # type: ignore[misc]
        is_ratio = (verify_result.effect_type or "").strip().lower() \
            in RATIO_EFFECT_TYPES
        null_val = 1.0 if is_ratio else 0.0
        excludes = (lo > null_val) or (hi < null_val)
        reasons.append(
            f"ci=[{lo},{hi}] "
            f"{'excludes' if excludes else 'includes'} {null_val:g}")
    elif verify_result.ci is None:
        reasons.append("ci=None (missing → stat_validity fails)")
    else:
        reasons.append(f"ci={verify_result.ci} not two finite bounds "
                       "(stat_validity fails)")
    # n
    if n is None:
        reasons.append(f"n=None (missing; need n>={MIN_N})")
    elif _is_finite_number(n):
        reasons.append(f"n={n} {'>=' if int(n) >= MIN_N else '<'} "
                       f"min {MIN_N}")
    else:
        reasons.append(f"n={n} non-finite (need n>={MIN_N})")

    # ---------------- 2) effect meaningfulness (MID) ----------------
    effect = verify_result.effect
    mid = _mid_for(verify_result.effect_type, mid_thresholds)
    et = verify_result.effect_type or "unknown"
    if effect is None or not _is_finite_number(effect):
        effect_meaningful = False
        reasons.append(f"effect={effect} missing/non-finite "
                       f"(cannot clear MID {mid})")
    else:
        ef = float(effect)
        if (verify_result.effect_type or "").strip().lower() \
                in RATIO_EFFECT_TYPES:
            fold = max(ef, 1.0 / ef) if ef > 0 else float("inf")
            effect_meaningful = fold >= mid
            reasons.append(
                f"effect={ef} (effect_type={et}); fold-change from 1.0="
                f"{fold:.4g} {'>=' if effect_meaningful else '<'} MID {mid}")
        else:
            effect_meaningful = abs(ef) >= mid
            reasons.append(
                f"effect={ef} (effect_type={et}); |effect|={abs(ef):g} "
                f"{'>=' if effect_meaningful else '<'} MID {mid}")

    # ---------------- 3) multiplicity ----------------
    multiplicity_ok, mult_reason = _check_multiplicity(verify_result)
    reasons.append(mult_reason)

    # ---------------- 4) novelty (provisional, non-gating) ----------------
    novelty = _novelty_note(hypothesis, llm)
    reasons.append(f"novelty={novelty}")

    # ---------------- artifact traceback (V8 §11) ----------------
    if verify_result.artifact_paths:
        reasons.append("evidence artifacts: "
                       + ", ".join(verify_result.artifact_paths))
    else:
        reasons.append("evidence artifacts: none registered")
    if verify_result.dataset_hash or verify_result.seed is not None:
        reasons.append(f"reproducibility: seed={verify_result.seed}, "
                       f"dataset_hash={verify_result.dataset_hash}")

    # ---------------- verdict ----------------
    verdict = "pass" if (stat_validity and effect_meaningful
                         and multiplicity_ok) else "fail"

    result = ReviewResult(
        stat_validity=stat_validity,
        effect_meaningful=effect_meaningful,
        multiplicity_ok=multiplicity_ok,
        novelty_provisional=novelty,
        verdict=verdict,
        reasons=reasons,
    )

    if private_bb is not None:
        private_bb.put("review_result", result.to_dict(),
                       producer="review_stage", seed=verify_result.seed)

    if bus is not None:
        bus.emit(SignalType.Done, source="review_stage",
                 hypothesis_id=verify_result.hypothesis_id,
                 verdict=verdict)

    return result
