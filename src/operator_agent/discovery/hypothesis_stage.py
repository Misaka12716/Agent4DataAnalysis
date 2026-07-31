# -*- coding: utf-8 -*-
"""N3 HYPOTHESIS-generation agent (decoupled) — V8 §5 / plan ``hypothesis-stage``.

This stage *reuses* the Stage-2 HYPOTHESIZE agent
(:func:`operator_agent.hypothesis_agent.propose_hypothesis`) as its default
generator, sampling it repeatedly (varying temperature / seed) to gather
multiple candidate hypotheses.  The generator is exposed through the ``impl``
injection point so tests can substitute a fake (no live LLM required).

Responsibilities (and nothing else — this agent is single-purpose):

1. Sample ``impl`` repeatedly until "enough" hypotheses are collected.
2. De-duplicate near-identical hypotheses so they don't each open a lane and
   multiply downstream context (V8 §11 dedup rule).
3. Publish *all* surviving hypotheses to the public blackboard.
4. Fan out one :class:`~operator_agent.discovery.blackboard.PrivateBlackboard`
   per surviving hypothesis (one lane each), seeded with that hypothesis.

Graceful degradation: if the LLM is unavailable (or the fake yields nothing
usable) the stage emits a single ``Error`` signal and returns empty lists —
it never raises (the supervisor decides what to do).
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

import pandas as pd

from operator_agent.hypothesis import Hypothesis
from operator_agent.hypothesis_agent import propose_hypothesis

from .blackboard import PrivateBlackboard, PublicBlackboard
from .novelty import DEFAULT_DROP_VERDICTS, assess as assess_novelty, passes_gate
from .paths import ensure_run_dir
from .signals import SignalBus

__all__ = ["run", "PRODUCER"]

PRODUCER = "hypothesis_stage"

# --- sampling knobs (documented constants) --------------------------------
# Each attempt nudges the temperature up so repeated calls to a stochastic
# generator explore different hypotheses instead of returning the same card.
_BASE_TEMPERATURE = 0.0
_TEMP_STEP = 0.15
_MAX_TEMPERATURE = 1.0
# "Enough" needs at least this many unique hypotheses before the
# family-diversity short-circuit may fire (so we never stop on a single one).
_MIN_FOR_DIVERSITY_STOP = 2


def _dedup_key(h: Hypothesis):
    """Dedup key (V8 §11): ``(sorted variables, finding_family,
    primary_outcome)``.  Two hypotheses with the same controlled variables,
    finding family and primary outcome are treated as duplicates and only the
    first is kept (avoids near-duplicate lanes multiplying context)."""
    return (
        tuple(sorted(h.variables or [])),
        h.finding_family,
        h.primary_outcome,
    )


def _invoke_impl(impl: Callable, task: str, df: pd.DataFrame,
                 *, temperature: float, seed: Optional[int]) -> Any:
    """Call ``impl`` passing only the kwargs it actually accepts.

    ``propose_hypothesis`` takes ``temperature`` (keyword-only) but no
    ``seed``; a fake generator may take ``**kwargs`` or neither.  We inspect
    the signature so the same call site works for all of them.
    """
    kwargs = {}
    try:
        params = inspect.signature(impl).parameters
        accepts_var_kw = any(
            p.kind == p.VAR_KEYWORD for p in params.values())
        if accepts_var_kw or "temperature" in params:
            kwargs["temperature"] = temperature
        if accepts_var_kw or "seed" in params:
            kwargs["seed"] = seed
    except (TypeError, ValueError):
        # No introspectable signature (e.g. some builtins): call plainly.
        pass
    return impl(task, df, **kwargs)


def _extract_hypothesis(result: Any) -> Optional[Hypothesis]:
    """Pull a usable :class:`Hypothesis` out of an ``impl`` return value.

    Accepts either a ``HypothesisResult``-like object (``.ok`` / ``.hypothesis``)
    or a bare :class:`Hypothesis`.  Returns ``None`` when nothing usable.
    """
    if result is None:
        return None
    if isinstance(result, Hypothesis):
        return result
    if getattr(result, "ok", True) is False:
        return None
    hyp = getattr(result, "hypothesis", None)
    return hyp if isinstance(hyp, Hypothesis) else None


def _extract_ideation(result: Any) -> Optional[dict]:
    """Pull the call-1 ideation step out of a two-step generator result.

    Returns a small dict ``{idea, is_finding, rationale, raw}`` when the
    impl ran the two-step path (the canonical ``propose_hypothesis_twostep``
    populates ``result.raw["ideation"]``), or ``None`` for legacy / fake
    impls that don't expose ideation as a separate step.

    Keeping this opt-in means swapping in a different ``impl`` (e.g. the
    single-call ``propose_hypothesis``) does NOT break the lane writer —
    ``None`` simply means "no ideation step recorded" and is allowed.
    """
    if result is None or isinstance(result, Hypothesis):
        return None
    raw = getattr(result, "raw", None)
    if not isinstance(raw, dict):
        return None
    ideation_raw = raw.get("ideation")
    if not isinstance(ideation_raw, dict):
        return None
    idea = str(ideation_raw.get("idea") or "").strip()
    is_finding = ideation_raw.get("is_finding")
    rationale = str(getattr(result, "rationale", "") or "").strip()
    out = {
        "idea": idea,
        "is_finding": (bool(is_finding)
                       if isinstance(is_finding, bool)
                       else (str(is_finding).strip().lower()
                             in {"true", "yes", "1"}
                             if is_finding is not None else None)),
        "rationale": rationale,
        "raw": ideation_raw,
    }
    return out


def run(
    task: str,
    df: pd.DataFrame,
    public_bb: PublicBlackboard,
    *,
    run_dir: Optional[Path] = None,
    max_hypotheses: int = 5,
    min_family_diversity: int = 3,
    max_attempts: int = 12,
    impl: Optional[Callable] = None,
    bus: Optional[SignalBus] = None,
    seed: Optional[int] = None,
    llm: Any = None,
    novelty_checker: Optional[Callable] = None,
    llm_novelty: bool = False,
    drop_replications: bool = True,
    novelty_min_score: float = 0.0,
) -> Tuple[List[Hypothesis], List[PrivateBlackboard]]:
    """Generate, de-duplicate and fan out hypotheses.

    Parameters
    ----------
    task, df
        The research task + dataframe handed to the generator.
    public_bb
        The run's public blackboard; the full surviving hypothesis set is
        written here under key ``"hypotheses"``.
    run_dir
        If given, lane blackboards are persisted under
        ``<run_dir>/lanes/<hid>.json``; otherwise they stay in-process
        (``path=None``).
    max_hypotheses
        Hard cap — stop once this many *unique* hypotheses exist.
    min_family_diversity
        Stop early once this many distinct ``finding_family`` values are
        represented (and at least ``_MIN_FOR_DIVERSITY_STOP`` unique
        hypotheses exist).
    max_attempts
        Upper bound on generator calls regardless of yield.
    impl
        Generator callable; defaults to ``propose_hypothesis``.  This is the
        injection point that lets tests supply a fake (no live LLM).
    bus
        Optional signal bus; an ``Error`` is emitted if no hypotheses survive.
    seed
        Optional base seed; stamped into public-blackboard provenance and
        offset per attempt for the generator.
    llm
        Optional LLM object (``is_available()`` + ``chat_json(...)``) used by
        the novelty gate's LLM-prior fallback (only when ``llm_novelty``).
    novelty_checker
        Optional literature backend ``(hypothesis) -> NoveltyAssessment |
        dict | None`` (the N6 retriever).  When given, the novelty gate
        consults it for every surviving hypothesis.
    llm_novelty
        Use the LLM-prior novelty fallback when ``novelty_checker`` is absent.
        Default ``False`` (a normal run adds no extra LLM calls).
    drop_replications
        When True (default) a hypothesis a backend *confidently* labels a
        ``"replication"`` is dropped before it opens a lane.  With no backend
        every verdict is ``"unknown"`` so nothing is dropped.
    novelty_min_score
        Drop hypotheses whose novelty ``score`` is below this (default
        ``0.0`` → never drop on score alone).

    Returns
    -------
    (unique_hypotheses, private_boards)
        ``private_boards[i]`` is the lane for ``unique_hypotheses[i]`` — the
        novelty-gated set (replications removed when a backend is present).

    "Enough" rule (documented):
        Stop as soon as ANY of these holds, re-checked before each attempt:
          (a) ``len(unique) >= max_hypotheses``, OR
          (b) distinct ``finding_family`` count ``>= min_family_diversity``
              AND ``len(unique) >= _MIN_FOR_DIVERSITY_STOP`` (i.e. >= 2), OR
          (c) ``max_attempts`` generator calls have been made.
    """
    generator = impl if impl is not None else propose_hypothesis

    unique: List[Hypothesis] = []
    # Parallel list: ``ideation_by_id[h.id]`` holds the call-1 ideation step
    # for two-step generators (``None`` when the impl does not expose one).
    # Keyed by hypothesis id so the gate / fan-out below can rebuild it
    # without re-aligning indices when the gate drops some hypotheses.
    ideation_by_id: dict = {}
    seen_keys = set()
    # Track the last hard error reported by the generator (e.g. an LLM
    # ``insufficient_quota`` carried on ``HypothesisResult.error``) so that,
    # if no hypothesis survives, we can surface *why* instead of an opaque
    # "no_hypotheses".
    last_gen_error: Optional[str] = None
    # Distinguish the *honest* "the data has no testable finding" case from a
    # failure: when the generator returns a valid response that simply omits a
    # hypothesis card (a generic data/annotation table with no outcome /
    # treatment columns), there is no error — but we must still tell the user
    # *why* nothing was produced.  Track how many attempts classified the task
    # as "not a finding" and keep the last rationale the LLM gave.
    n_no_card = 0
    last_no_card_rationale: Optional[str] = None
    last_warning: Optional[str] = None

    def _enough() -> bool:
        if len(unique) >= max_hypotheses:
            return True
        families = {h.finding_family for h in unique}
        if (len(families) >= min_family_diversity
                and len(unique) >= _MIN_FOR_DIVERSITY_STOP):
            return True
        return False

    for attempt in range(max_attempts):
        if _enough():
            break
        temperature = min(_MAX_TEMPERATURE,
                          _BASE_TEMPERATURE + _TEMP_STEP * attempt)
        attempt_seed = (seed + attempt) if seed is not None else None
        try:
            result = _invoke_impl(generator, task, df,
                                  temperature=temperature, seed=attempt_seed)
        except Exception as exc:  # generator blew up — record + keep trying
            if bus is not None:
                bus.emit_error(PRODUCER, reason="impl_exception",
                               error=repr(exc), attempt=attempt)
            continue

        # Capture a hard generator error (ok is False / .error set) so the
        # underlying cause is not silently dropped when nothing survives.
        gen_err = getattr(result, "error", None)
        if gen_err:
            last_gen_error = str(gen_err)

        hyp = _extract_hypothesis(result)
        if hyp is None:
            # No hard error but also no card → the generator judged the task
            # as a generic data task (no testable finding).  Remember why so
            # a 0-hypothesis run can explain itself instead of looking broken.
            if gen_err is None:
                n_no_card += 1
                rationale = str(getattr(result, "rationale", "") or "").strip()
                if rationale:
                    last_no_card_rationale = rationale
                warning = getattr(result, "warning", None)
                if warning:
                    last_warning = str(warning)
            continue
        key = _dedup_key(hyp)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(hyp)
        # Capture the call-1 ideation step (if the impl exposed one) so the
        # lane can persist *what the LLM proposed in plain English* before
        # the schema-shape step possibly reworded it.  ``None`` is fine.
        ideation = _extract_ideation(result)
        if ideation is not None:
            ideation_by_id[hyp.id] = ideation

    if not unique:
        if bus is not None:
            payload = {"reason": "no_hypotheses", "attempts": max_attempts}
            if last_gen_error:
                # Echo the generator's own failure (e.g. the LLM error) so the
                # supervisor / user can see the real cause.
                payload["error"] = last_gen_error
            elif n_no_card:
                # Honest "no testable finding in this data" outcome (not a
                # failure): the generator ran fine but classified every attempt
                # as a generic data task.  Surface the count + the LLM's own
                # explanation so the user knows the data, not the system, is
                # the reason.
                detail = (f"generator classified all {n_no_card} attempt(s) "
                          f"as a generic data task (no testable scientific "
                          f"finding in this dataset)")
                if last_no_card_rationale:
                    detail += f"; last rationale: {last_no_card_rationale}"
                if last_warning:
                    detail += f"; last warning: {last_warning}"
                payload["error"] = detail
            bus.emit_error(PRODUCER, **payload)
        return [], []

    # ----------------------- novelty gate (V8 N6 @ N3) ------------------
    # Judge each surviving hypothesis against the literature BEFORE it opens a
    # verification lane, so we don't spend budget re-deriving published work.
    # With no backend every verdict is "unknown" → nothing is dropped.
    drop_verdicts = DEFAULT_DROP_VERDICTS if drop_replications else ()
    kept: List[Hypothesis] = []
    novelty_by_id: dict = {}
    gated_out: List[dict] = []
    for h in unique:
        assessment = assess_novelty(
            h, checker=novelty_checker, llm=llm, llm_novelty=llm_novelty)
        novelty_by_id[h.id] = assessment
        if passes_gate(assessment, min_score=novelty_min_score,
                       drop_verdicts=drop_verdicts):
            kept.append(h)
        else:
            gated_out.append({"hypothesis_id": h.id,
                              "novelty": assessment.to_dict()})

    # Record what the gate dropped (traceability) — never silently discard.
    if gated_out and bus is not None:
        bus.emit_done(PRODUCER, phase="novelty_gate",
                      n_dropped=len(gated_out), dropped=gated_out)

    if not kept:
        # Every hypothesis was a confident replication → no NOVEL finding to
        # pursue.  Surface it as an honest, explained outcome (not a crash).
        if bus is not None:
            detail = (f"all {len(unique)} hypothesis(es) were gated out by the "
                      f"novelty check (already-published / below novelty "
                      f"threshold {novelty_min_score})")
            bus.emit_error(PRODUCER, reason="no_novel_hypotheses",
                           error=detail, dropped=gated_out)
        return [], []

    # Publish the GATED set to the public blackboard (read in bulk only by
    # routing / aggregation; each lane reads just its own private board).
    public_bb.put("hypotheses", [h.to_dict() for h in kept],
                  producer=PRODUCER, seed=seed)
    public_bb.put("novelty",
                  {h.id: novelty_by_id[h.id].to_dict() for h in kept},
                  producer=PRODUCER, seed=seed)

    # Resolve lane paths (materialise the run tree only when run_dir given).
    run_paths_obj = None
    if run_dir is not None:
        rd = Path(run_dir)
        run_paths_obj = ensure_run_dir(rd.name, runs_root=rd.parent)

    private_boards: List[PrivateBlackboard] = []
    for h in kept:
        lane_path = run_paths_obj.lane_path(h.id) if run_paths_obj else None
        priv = PrivateBlackboard(h.id, path=lane_path)
        priv.put("hypothesis", h.to_dict(), producer=PRODUCER)
        # Seed the lane with its novelty assessment so review / compile can
        # carry literature context into findings.yaml.
        priv.put("novelty", novelty_by_id[h.id].to_dict(), producer=PRODUCER)
        # Persist the call-1 ideation step (when available) so debugging /
        # auditing a "weak hypothesis" doesn't need to re-run the LLM —
        # readers can see EXACTLY what the LLM proposed in plain English
        # before the schema-shape step.  Two-step generators populate this;
        # legacy single-call generators leave it absent.
        ideation = ideation_by_id.get(h.id)
        if ideation is not None:
            priv.put("ideation", ideation, producer=PRODUCER)
        if lane_path is not None:
            try:
                priv.save_json()
            except Exception:  # persistence is best-effort, never fatal
                pass
        private_boards.append(priv)

    if bus is not None:
        bus.emit_done(PRODUCER, n_hypotheses=len(kept))

    return kept, private_boards
