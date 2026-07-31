# -*- coding: utf-8 -*-
"""Contract layer for the discovery framework — every blackboard entry and
stage output as a JSON-safe dataclass.

Design rules (V8 §11 — context budget + number credibility)
------------------------------------------------------------
- Every dataclass has ``to_dict()`` / ``from_dict()`` that round-trip
  through ``json.dumps``/``json.loads`` losslessly.
- Numbers that drive credibility (``effect`` / ``ci`` / ``p`` / ``n`` /
  ``seed`` / ``dataset_hash`` / ``operator_versions``) are stored verbatim
  on :class:`VerifyResult`; the blackboard ``compress()`` step must never
  touch them.
- Large products live on disk; dataclasses only carry **paths + short
  summaries**, never payloads.

The :class:`Hypothesis` card is *reused* from
:mod:`operator_agent.hypothesis` (not redefined here).
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

from operator_agent.hypothesis import Hypothesis

__all__ = [
    "RequirementSummary",
    "ProfileSummary",
    "CleanSuggestion",
    "Evidence",
    "VerifyResult",
    "RefineDecision",
    "ReviewResult",
    "NoveltyAssessment",
    "FindingRecord",
    "DiscoveryResult",
]


# ---------------------------------------------------------------------------
# Per-operator evidence (ADR-0008)
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class Evidence:
    """Statistical evidence extracted from a SINGLE operator's output.

    ADR-0008 — verify_stage no longer first-match-merges across operators.
    Each known bio operator (limma_deg / pathway_enrichment / ...) has a
    per-operator extractor that produces one of these.  Unknown operators
    fall back to a flat scan but still produce one Evidence per operator,
    not a merged result.

    Attributes
    ----------
    source_operator
        Operator id (e.g. ``"limma_deg_two_group"``,
        ``"pathway_enrichment_fisher"``).  Required.
    effect / effect_kind
        Numerical effect + its semantic label (e.g. ``"logFC"``,
        ``"fold_enrichment"``, ``"OR"``).  Either both populated or
        both ``None``.
    p / p_kind
        p-value + which kind (e.g. ``"raw_p"``, ``"adj_p"``).  Either
        both populated or both ``None``.
    n / n_kind
        Sample-size-like number + which kind (e.g. ``"n_target"``,
        ``"n_overlap"``, ``"n_total"``).
    raw
        Whole operator-result dict, kept verbatim so review_stage
        and audit consumers can re-derive numbers.
    """
    source_operator: str
    effect: Optional[float] = None
    effect_kind: Optional[str] = None
    p: Optional[float] = None
    p_kind: Optional[str] = None
    n: Optional[int] = None
    n_kind: Optional[str] = None
    raw: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_operator": self.source_operator,
            "effect": self.effect,
            "effect_kind": self.effect_kind,
            "p": self.p,
            "p_kind": self.p_kind,
            "n": self.n,
            "n_kind": self.n_kind,
            "raw": dict(self.raw),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Evidence":
        n_raw = d.get("n")
        return cls(
            source_operator=d.get("source_operator", "?"),
            effect=_float_or_none(d.get("effect")),
            effect_kind=d.get("effect_kind"),
            p=_float_or_none(d.get("p")),
            p_kind=d.get("p_kind"),
            n=int(n_raw) if n_raw is not None else None,
            n_kind=d.get("n_kind"),
            raw=dict(d.get("raw") or {}),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hypothesis_to_dict(h: Optional[Hypothesis]) -> Optional[Dict[str, Any]]:
    return h.to_dict() if h is not None else None


def _hypothesis_from_dict(
        d: Optional[Dict[str, Any]]) -> Optional[Hypothesis]:
    return Hypothesis.from_dict(d) if d else None


def _float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ci_to_list(ci: Any) -> Optional[List[float]]:
    if ci is None:
        return None
    try:
        lo, hi = ci  # tuple/list of two
        return [float(lo), float(hi)]
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Supervisor → public blackboard
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class RequirementSummary:
    """Supervisor's bounded summary of the user's research requirement."""
    task: str
    goal: str = ""
    dataset_description: Optional[str] = None
    constraints: List[str] = dataclasses.field(default_factory=list)
    success_criteria: List[str] = dataclasses.field(default_factory=list)
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "goal": self.goal,
            "dataset_description": self.dataset_description,
            "constraints": list(self.constraints),
            "success_criteria": list(self.success_criteria),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RequirementSummary":
        return cls(
            task=d.get("task", ""),
            goal=d.get("goal", ""),
            dataset_description=d.get("dataset_description"),
            constraints=list(d.get("constraints") or []),
            success_criteria=list(d.get("success_criteria") or []),
            notes=d.get("notes"),
        )


# ---------------------------------------------------------------------------
# Data-processing agent → public blackboard
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class ProfileSummary:
    """Data-profile summary (path + short prose, never the whole table).

    ``inferred_shape`` is the optional output of
    :func:`operator_agent.discovery.data_shape_inference.infer_shape` — a
    short structured guess at *what kind* of dataset this is (DEG result,
    parallel-arm trial, case-control, survival, …).  It is stored alongside
    the raw profile so downstream agents (and human reviewers) can see the
    same hint that was prepended to ``profile_text``; ``None`` when no rule
    fired.
    """
    n_rows: int = 0
    n_cols: int = 0
    columns: List[str] = dataclasses.field(default_factory=list)
    dtypes: Dict[str, str] = dataclasses.field(default_factory=dict)
    profile_text: str = ""
    profile_path: Optional[str] = None
    inferred_shape: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_rows": int(self.n_rows),
            "n_cols": int(self.n_cols),
            "columns": list(self.columns),
            "dtypes": dict(self.dtypes),
            "profile_text": self.profile_text,
            "profile_path": self.profile_path,
            "inferred_shape": (dict(self.inferred_shape)
                               if self.inferred_shape else None),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProfileSummary":
        return cls(
            n_rows=int(d.get("n_rows") or 0),
            n_cols=int(d.get("n_cols") or 0),
            columns=list(d.get("columns") or []),
            dtypes=dict(d.get("dtypes") or {}),
            profile_text=d.get("profile_text") or "",
            profile_path=d.get("profile_path"),
            inferred_shape=(dict(d["inferred_shape"])
                            if d.get("inferred_shape") else None),
        )


@dataclasses.dataclass
class CleanSuggestion:
    """A single, *non-executed* cleaning suggestion for one column/issue."""
    column: str
    issue: str            # e.g. "missing" | "outlier" | "type" | "duplicate"
    suggestion: str
    severity: str = "low"  # "low" | "medium" | "high"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "column": self.column,
            "issue": self.issue,
            "suggestion": self.suggestion,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CleanSuggestion":
        return cls(
            column=d.get("column", ""),
            issue=d.get("issue", ""),
            suggestion=d.get("suggestion", ""),
            severity=d.get("severity", "low"),
        )


# ---------------------------------------------------------------------------
# Verify (data-analysis) agent → private blackboard
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class VerifyResult:
    """Reproducible statistical evidence for one verification run.

    The number-credibility contract (V8 §11): ``effect`` / ``ci`` / ``p`` /
    ``n`` / ``seed`` / ``dataset_hash`` / ``operator_versions`` /
    ``artifact_paths`` are stored verbatim and survive ``compress()``.

    ``status`` is one of:
        - ``"ok"``           — ran, produced usable numbers.
        - ``"inconclusive"`` — ran but result is weak/NaN/underpowered
                                (NOT an execution error; goes to refine).
        - ``"error"``        — execution failure; ``error`` carries the
                                structured error contract (see
                                :mod:`operator_pipeline.error_codes`).
    """
    status: str = "ok"
    effect: Optional[float] = None
    ci: Optional[List[float]] = None              # [low, high]
    p: Optional[float] = None
    n: Optional[int] = None
    seed: Optional[int] = None
    dataset_hash: Optional[str] = None
    operator_versions: Dict[str, str] = dataclasses.field(default_factory=dict)
    artifact_paths: List[str] = dataclasses.field(default_factory=list)
    error: Optional[Dict[str, Any]] = None        # error_codes-style contract
    # Optional convenience fields (not part of the hard contract).
    hypothesis_id: Optional[str] = None
    effect_type: Optional[str] = None             # e.g. "ATE" | "beta" | "OR"
    extra: Dict[str, Any] = dataclasses.field(default_factory=dict)
    # ADR-0008 — per-operator evidence list (additive; populated by
    # verify_stage's per-operator extractor table).  Single-value
    # ``effect``/``p``/``n`` above are filled from the **primary**
    # operator's Evidence (see verify_stage).  This list preserves
    # every operator's contribution untouched, so review_stage can
    # later upgrade to multi-evidence verdicts without reshaping
    # VerifyResult.
    evidence_per_operator: List["Evidence"] = dataclasses.field(
        default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "effect": self.effect,
            "ci": list(self.ci) if self.ci is not None else None,
            "p": self.p,
            "n": self.n,
            "seed": self.seed,
            "dataset_hash": self.dataset_hash,
            "operator_versions": dict(self.operator_versions),
            "artifact_paths": list(self.artifact_paths),
            "error": dict(self.error) if self.error is not None else None,
            "hypothesis_id": self.hypothesis_id,
            "effect_type": self.effect_type,
            "extra": dict(self.extra),
            "evidence_per_operator": [e.to_dict()
                                      for e in self.evidence_per_operator],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VerifyResult":
        n_raw = d.get("n")
        seed_raw = d.get("seed")
        return cls(
            status=d.get("status", "ok"),
            effect=_float_or_none(d.get("effect")),
            ci=_ci_to_list(d.get("ci")),
            p=_float_or_none(d.get("p")),
            n=int(n_raw) if n_raw is not None else None,
            seed=int(seed_raw) if seed_raw is not None else None,
            dataset_hash=d.get("dataset_hash"),
            operator_versions=dict(d.get("operator_versions") or {}),
            artifact_paths=list(d.get("artifact_paths") or []),
            error=dict(d["error"]) if d.get("error") is not None else None,
            hypothesis_id=d.get("hypothesis_id"),
            effect_type=d.get("effect_type"),
            extra=dict(d.get("extra") or {}),
            evidence_per_operator=[Evidence.from_dict(e)
                                   for e in (d.get("evidence_per_operator")
                                             or [])],
        )


# ---------------------------------------------------------------------------
# Refine (lane-local) → control
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class RefineDecision:
    """Lane-local decision after inspecting a VerifyResult vs Hypothesis."""
    action: str                                   # "refine"|"converge"|"giveup"
    revised: Optional[Hypothesis] = None          # populated when action=refine
    reason: Optional[str] = None
    iteration: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "revised": _hypothesis_to_dict(self.revised),
            "reason": self.reason,
            "iteration": self.iteration,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RefineDecision":
        return cls(
            action=d.get("action", "giveup"),
            revised=_hypothesis_from_dict(d.get("revised")),
            reason=d.get("reason"),
            iteration=d.get("iteration"),
        )


# ---------------------------------------------------------------------------
# Review agent → private blackboard
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class ReviewResult:
    """Reviewer verdict — cites only numbers from a VerifyResult.

    ``novelty_provisional`` is a *provisional* label (LLM prior, no DB);
    statistical credibility (the first three flags) is hard and never
    depends on novelty.
    """
    stat_validity: bool = False
    effect_meaningful: bool = False
    multiplicity_ok: bool = False
    novelty_provisional: Optional[str] = None     # e.g. "novel"|"known"|None
    verdict: str = "fail"                          # "pass" | "fail"
    reasons: List[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stat_validity": bool(self.stat_validity),
            "effect_meaningful": bool(self.effect_meaningful),
            "multiplicity_ok": bool(self.multiplicity_ok),
            "novelty_provisional": self.novelty_provisional,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ReviewResult":
        return cls(
            stat_validity=bool(d.get("stat_validity", False)),
            effect_meaningful=bool(d.get("effect_meaningful", False)),
            multiplicity_ok=bool(d.get("multiplicity_ok", False)),
            novelty_provisional=d.get("novelty_provisional"),
            verdict=d.get("verdict", "fail"),
            reasons=list(d.get("reasons") or []),
        )


# ---------------------------------------------------------------------------
# Novelty gate (applied at the HYPOTHESIS-PROPOSING stage, N3)
# ---------------------------------------------------------------------------
#: Allowed novelty verdicts (ordered most→least novel).  ``unknown`` is the
#: safe default when no literature backend is available — it never gates.
NOVELTY_VERDICTS = (
    "candidate_novel",   # no prior found → worth verifying
    "incremental",       # partial / indirect prior exists
    "replication",       # the core claim is already published
    "unknown",           # not assessed (no literature DB / no LLM)
)


@dataclasses.dataclass
class NoveltyAssessment:
    """Literature-novelty judgement for ONE hypothesis (V8 §N6 gate at N3).

    The gate runs *before* a hypothesis opens a verification lane so the
    framework does not spend operator/LLM budget re-deriving already-published
    findings.  It is deliberately decoupled from any specific retrieval
    backend: a real PubMed / Europe PMC / KG retriever plugs in as an
    injectable ``checker`` (see :mod:`operator_agent.discovery.novelty`); with
    no backend the verdict is ``"unknown"`` and the gate never drops anything.

    Attributes
    ----------
    verdict
        One of :data:`NOVELTY_VERDICTS`.
    score
        Continuous novelty score in ``[0, 1]``: ``0`` = certainly already
        published, ``1`` = no prior evidence found.  ``unknown`` ⇒ ``0.5``.
    query
        The literature query string the assessment was based on.
    references
        Retrieved supporting/conflicting references; each a small dict
        (``title`` / ``id`` / ``source`` / ``stance`` / ``relevance``).  Paths
        not payloads (V8 §11 context budget).
    rationale
        One-line human-readable justification.
    source
        Provenance of the judgement: ``"stub"`` (no backend), ``"llm_prior"``
        (LLM guess, no DB), or the retriever name (e.g. ``"pubmed"``).
    """
    verdict: str = "unknown"
    score: float = 0.5
    query: str = ""
    references: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    rationale: str = ""
    source: str = "stub"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "score": float(self.score),
            "query": self.query,
            "references": [dict(r) for r in self.references],
            "rationale": self.rationale,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NoveltyAssessment":
        d = d or {}
        verdict = d.get("verdict", "unknown")
        if verdict not in NOVELTY_VERDICTS:
            verdict = "unknown"
        score = _float_or_none(d.get("score"))
        return cls(
            verdict=verdict,
            score=0.5 if score is None else score,
            query=d.get("query", ""),
            references=list(d.get("references") or []),
            rationale=d.get("rationale", ""),
            source=d.get("source", "stub"),
        )


# ---------------------------------------------------------------------------
# Compile → findings.yaml  (V8 §F)
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class FindingRecord:
    """One compiled finding (§F).  ``finding_id`` follows
    ``F_C{cohort}_N{i}``; ``statistical_evidence`` carries the verbatim
    numbers; ``reproducibility`` carries the checksums for one-click
    traceback.  ``literature_context`` is None in this phase (N6 stub).
    """
    finding_id: str
    hypothesis: Optional[Hypothesis] = None
    statistical_evidence: Dict[str, Any] = dataclasses.field(
        default_factory=dict)
    review_result: Optional[ReviewResult] = None
    literature_context: Optional[Dict[str, Any]] = None   # N6 → None
    reproducibility: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "hypothesis": _hypothesis_to_dict(self.hypothesis),
            "statistical_evidence": dict(self.statistical_evidence),
            "review_result": (self.review_result.to_dict()
                              if self.review_result is not None else None),
            "literature_context": (dict(self.literature_context)
                                   if self.literature_context is not None
                                   else None),
            "reproducibility": dict(self.reproducibility),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FindingRecord":
        rr = d.get("review_result")
        return cls(
            finding_id=d.get("finding_id", ""),
            hypothesis=_hypothesis_from_dict(d.get("hypothesis")),
            statistical_evidence=dict(d.get("statistical_evidence") or {}),
            review_result=ReviewResult.from_dict(rr) if rr else None,
            literature_context=(dict(d["literature_context"])
                                if d.get("literature_context") is not None
                                else None),
            reproducibility=dict(d.get("reproducibility") or {}),
        )

    # Convenience: build the evidence/reproducibility blocks straight from
    # a VerifyResult (keeps numbers verbatim).
    @classmethod
    def from_verify(cls, finding_id: str, hypothesis: Optional[Hypothesis],
                    verify: "VerifyResult",
                    review: Optional[ReviewResult] = None,
                    literature_context: Optional[Dict[str, Any]] = None
                    ) -> "FindingRecord":
        return cls(
            finding_id=finding_id,
            hypothesis=hypothesis,
            statistical_evidence={
                "effect": verify.effect,
                "effect_type": verify.effect_type,
                "ci": list(verify.ci) if verify.ci is not None else None,
                "p": verify.p,
                "n": verify.n,
            },
            review_result=review,
            literature_context=literature_context,
            reproducibility={
                "seed": verify.seed,
                "dataset_hash": verify.dataset_hash,
                "operator_versions": dict(verify.operator_versions),
                "artifact_paths": list(verify.artifact_paths),
            },
        )


# ---------------------------------------------------------------------------
# Supervisor final output
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class DiscoveryResult:
    """Top-level result of a discovery run.

    ADR-0007 — ``status`` now spans 5 values (see :class:`RunStatus`):

    - ``ok``             — at least one finding produced.
    - ``empty``          — ran cleanly but produced no findings.
    - ``cancelled``      — user explicitly cancelled (UserActionError).
    - ``rejected_input`` — user-input shape error (DataInputError).
    - ``error``          — agent / system / unclassified failure.

    ``error`` is **only** populated when ``status == "error"``; it carries
    the ``supervisor_uncaught: ...`` string.  ``reason`` is the
    user-readable explanation for any non-``ok`` status (e.g.
    ``"user clicked cancel during verify_stage"``,
    ``"file too large: 850 MB exceeds 500 MB cap"``).  ``partial`` is
    True iff ``status in {cancelled, error}`` AND ``findings`` is
    non-empty (some findings made it through review before termination).
    """
    run_id: str
    route: str = "discovery"                       # "discovery" | "general"
    status: str = "ok"                             # one of RunStatus values
    findings: List[FindingRecord] = dataclasses.field(default_factory=list)
    findings_path: Optional[str] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    reason: Optional[str] = None
    partial: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "route": self.route,
            "status": self.status,
            "findings": [f.to_dict() for f in self.findings],
            "findings_path": self.findings_path,
            "summary": self.summary,
            "error": self.error,
            "reason": self.reason,
            "partial": bool(self.partial),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DiscoveryResult":
        return cls(
            run_id=d.get("run_id", ""),
            route=d.get("route", "discovery"),
            status=d.get("status", "ok"),
            findings=[FindingRecord.from_dict(f)
                      for f in (d.get("findings") or [])],
            findings_path=d.get("findings_path"),
            summary=d.get("summary"),
            error=d.get("error"),
            reason=d.get("reason"),
            partial=bool(d.get("partial", False)),
        )
