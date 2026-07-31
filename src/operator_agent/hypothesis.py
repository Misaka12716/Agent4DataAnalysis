# -*- coding: utf-8 -*-
"""V8 Hypothesis 卡片 — Stage 2 HYPOTHESIZE 的结构化输出.

V7 (现在) 的 planner 只输出 ``{rationale, steps}``, 没有 hypothesis
schema.  V8 §C 强制 hypothesis 卡片新增 4 个字段, 让下游 (路由 / budget /
评估 / bias-check) 都能机器可读地拿到这些信号.

字段 (与 V8 ``docs/v8_AGENT_DESIGN.md`` §C 一一对应)
----------------------------------------------------
- ``finding_family`` (str enum, 20 种 + ``other``)
    finding 类型, 给 planner-side 路由 (V8 §B.3) 和 bias-check (V8 §E) 用.
- ``expected_hops`` (1 | 2 | 3)
    推理深度.  V8 §C.2 不允许 4+ (真实精神医学顶刊罕见).
- ``expected_agent_workflow_length`` (int 5-30)
    估计要跑的 atomic action 数.  V8 §D.8 budget controller 按这个发预算.
- ``expected_modality`` (list[ str enum ], 12 种)
    finding 涉及的数据模态.  V8 §C.4 强制 ``genotype_single_snp`` 与
    ``genotype_prs`` 不共存 (优先 prs).

可选字段 (保留 V7 hypothesis 卡片的兼容字段)
- ``id``                str  — 假设 id, 若不提供则自动 ``H{int_uid}``
- ``variables``         list[str] — 涉及的变量名 (CSV 列名)
- ``edge_type``         str — ``causal`` / ``correlational`` / ``descriptive``
- ``expected_effect_direction`` str — 自由文本, 给评估对照
- ``rationale``         str — 1-3 句话解释
- ``decoy_or_novel``    str enum — ``novel`` / ``decoy`` / ``replication``

References
----------
- V8 §C.1-C.5 (finding_family / expected_hops / workflow_length / modality 完整 enum)
- V8 §E rubric #1-#8 (这些字段是 bias-check 的判据)
- V8 §D.8 (workflow_length → budget allocation)
- V8 §B.3 (finding_family → operator routing)
"""
from __future__ import annotations

import dataclasses
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Enums  (str enum, 防止小模型大小写 / 下划线 / 连字符乱出)
# ---------------------------------------------------------------------------

FINDING_FAMILY_VALUES: Tuple[str, ...] = (
    "pgx_interaction",
    "psychotherapy_comparison",
    "digital_intervention",
    "suicide_prediction",
    "functional_outcome",
    "prs_x_env",
    "imaging_eeg_biomarker",
    "inflammation_marker",
    "special_population",
    "neuromodulation_response",
    "treatment_resistant_subgroup",
    "rwe_drug_safety",
    "comorbidity_triad",
    "childhood_trauma_mediation",
    "sleep_comorbidity",
    "symptom_network",
    "subtyping_clustering",
    "mediation_chain",
    "mendelian_randomization",
    "other",
)

# LLM 常出错的家族别名 → 正式 enum (大小写不敏感, 下划线/连字符通用化后比较).
_FAMILY_ALIASES: Dict[str, str] = {
    "pgx":                       "pgx_interaction",
    "pharmacogenomics":          "pgx_interaction",
    "drug_gene_interaction":     "pgx_interaction",
    "drug_snp_interaction":      "pgx_interaction",
    "psychotherapy":             "psychotherapy_comparison",
    "therapy_comparison":        "psychotherapy_comparison",
    "cbt_comparison":            "psychotherapy_comparison",
    "treatment_comparison":      "psychotherapy_comparison",
    "digital_therapy":           "digital_intervention",
    "digital_therapeutics":      "digital_intervention",
    "app_intervention":          "digital_intervention",
    "ehealth":                   "digital_intervention",
    "mhealth":                   "digital_intervention",
    "suicide_risk":              "suicide_prediction",
    "suicide_risk_prediction":   "suicide_prediction",
    "self_harm_prediction":      "suicide_prediction",
    "function":                  "functional_outcome",
    "wsas":                      "functional_outcome",
    "sofas":                     "functional_outcome",
    "gaf":                       "functional_outcome",
    "whoqol":                    "functional_outcome",
    "quality_of_life":           "functional_outcome",
    "prs_env":                   "prs_x_env",
    "prs_environment":           "prs_x_env",
    "polygenic_x_env":           "prs_x_env",
    "gxe_prs":                   "prs_x_env",
    "imaging":                   "imaging_eeg_biomarker",
    "mri":                       "imaging_eeg_biomarker",
    "eeg":                       "imaging_eeg_biomarker",
    "neuroimaging":              "imaging_eeg_biomarker",
    "fmri":                      "imaging_eeg_biomarker",
    "inflammation":              "inflammation_marker",
    "crp":                       "inflammation_marker",
    "il6":                       "inflammation_marker",
    "cytokine":                  "inflammation_marker",
    "subgroup":                  "special_population",
    "perinatal":                 "special_population",
    "elderly":                   "special_population",
    "adolescent":                "special_population",
    "veteran":                   "special_population",
    "rtms":                      "neuromodulation_response",
    "tdcs":                      "neuromodulation_response",
    "mect":                      "neuromodulation_response",
    "ect":                       "neuromodulation_response",
    "neuromodulation":           "neuromodulation_response",
    "trd":                       "treatment_resistant_subgroup",
    "treatment_resistant":       "treatment_resistant_subgroup",
    "rwe":                       "rwe_drug_safety",
    "real_world_evidence":       "rwe_drug_safety",
    "pharmacoepidemiology":      "rwe_drug_safety",
    "drug_safety":               "rwe_drug_safety",
    "comorbidity":               "comorbidity_triad",
    "multi_comorbidity":         "comorbidity_triad",
    "trauma_mediation":          "childhood_trauma_mediation",
    "ace_mediation":             "childhood_trauma_mediation",
    "insomnia":                  "sleep_comorbidity",
    "sleep":                     "sleep_comorbidity",
    "network":                   "symptom_network",
    "symptom_net":               "symptom_network",
    "qgraph":                    "symptom_network",
    "cluster":                   "subtyping_clustering",
    "subtype":                   "subtyping_clustering",
    "phenotype_cluster":         "subtyping_clustering",
    "mediation":                 "mediation_chain",
    "chain_mediation":           "mediation_chain",
    "multi_mediator":            "mediation_chain",
    "mr":                        "mendelian_randomization",
    "ivw":                       "mendelian_randomization",
    "mendelian":                 "mendelian_randomization",
}


EXPECTED_MODALITY_VALUES: Tuple[str, ...] = (
    "clinical_scale",
    "genotype_single_snp",
    "genotype_prs",
    "genotype_pathway",
    "imaging_derived",
    "eeg_derived",
    "inflammation",
    "psychotherapy_intervention",
    "digital_intervention",
    "neuromodulation",
    "subgroup_population",
    "functional_outcome",
    "suicide_outcome",
    "other",
)

_MODALITY_ALIASES: Dict[str, str] = {
    "scale":                     "clinical_scale",
    "panss":                     "clinical_scale",
    "hamd":                      "clinical_scale",
    "phq9":                      "clinical_scale",
    "phq":                       "clinical_scale",
    "madrs":                     "clinical_scale",
    "qids":                      "clinical_scale",
    "snp":                       "genotype_single_snp",
    "single_snp":                "genotype_single_snp",
    "genotype":                  "genotype_single_snp",
    "prs":                       "genotype_prs",
    "polygenic_risk_score":      "genotype_prs",
    "polygenic":                 "genotype_prs",
    "pathway":                   "genotype_pathway",
    "gene_set":                  "genotype_pathway",
    "imaging":                   "imaging_derived",
    "mri":                       "imaging_derived",
    "fmri":                      "imaging_derived",
    "eeg":                       "eeg_derived",
    "alpha_power":               "eeg_derived",
    "p300":                      "eeg_derived",
    "inflammation_marker":       "inflammation",
    "crp":                       "inflammation",
    "il6":                       "inflammation",
    "il_6":                      "inflammation",
    "psychotherapy":             "psychotherapy_intervention",
    "cbt":                       "psychotherapy_intervention",
    "ipt":                       "psychotherapy_intervention",
    "dbt":                       "psychotherapy_intervention",
    "emdr":                      "psychotherapy_intervention",
    "digital":                   "digital_intervention",
    "app":                       "digital_intervention",
    "mhealth":                   "digital_intervention",
    "ehealth":                   "digital_intervention",
    "ect":                       "neuromodulation",
    "mect":                      "neuromodulation",
    "rtms":                      "neuromodulation",
    "tdcs":                      "neuromodulation",
    "subgroup":                  "subgroup_population",
    "population":                "subgroup_population",
    "perinatal":                 "subgroup_population",
    "elderly":                   "subgroup_population",
    "function":                  "functional_outcome",
    "wsas":                      "functional_outcome",
    "sofas":                     "functional_outcome",
    "suicide":                   "suicide_outcome",
    "c_ssrs":                    "suicide_outcome",
    "cssrs":                     "suicide_outcome",
}

EDGE_TYPE_VALUES: Tuple[str, ...] = ("causal", "correlational", "descriptive")
DECOY_VALUES: Tuple[str, ...] = ("novel", "decoy", "replication")

MIN_HOPS, MAX_HOPS = 1, 3
MIN_WF_LEN, MAX_WF_LEN = 5, 30


# ---------------------------------------------------------------------------
# G5 — Cohort → primary-outcome priority registry  (V8 §D.4)
# ---------------------------------------------------------------------------
# Each cohort theme has a *prioritised* list of primary outcomes; the
# hypothesis agent picks the highest-priority one supported by the data,
# or any explicitly named in the task.  The registry is descriptive
# (it documents canonical names + aliases), not prescriptive — the
# LLM is free to emit any string for ``primary_outcome``; we just
# canonicalise it for downstream routing.
#
# Cohort ids match V8 §0.5 / §D.4 ("C01" ... "C05").  Each entry is a
# tuple of (canonical_outcome_name, ordered list of aliases).
COHORT_PRIMARY_OUTCOMES: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = {
    "C01_schizophrenia": [
        ("panss_total_improvement",
         ("panss_total", "panss", "panss_improvement",
          "panss_change", "panss_decrease")),
        ("relapse_180d",
         ("relapse_6mo", "relapse_six_month", "schizophrenia_relapse",
          "relapse")),
        ("sofas_improvement",
         ("sofas", "sofas_change", "sofas_increase")),
        ("suicide_event",
         ("suicide_attempt", "suicide", "suicide_death")),
    ],
    "C02_mdd": [
        ("hamd_improvement",
         ("hamd", "hamd17", "hamd_17", "hamd_total",
          "hamd_change", "hamd_decrease")),
        ("phq9_improvement",
         ("phq9", "phq_9", "phq9_total", "phq9_change")),
        ("remission",
         ("hamd_remission", "phq9_remission", "depression_remission")),
        ("wsas_improvement",
         ("wsas", "wsas_change", "wsas_decrease")),
        ("suicide_attempt_90d",
         ("suicide_attempt", "suicide", "self_harm")),
    ],
    "C03_bipolar": [
        ("ymrs_plus_hamd_improvement",
         ("ymrs", "ymrs_hamd", "ymrs_change", "mood_score_change")),
        ("mood_recurrence",
         ("recurrence", "manic_recurrence", "depressive_recurrence",
          "episode_recurrence")),
        ("fast_improvement",
         ("fast", "fast_change", "functional_assessment")),
    ],
    "C04_ptsd": [
        ("caps5_improvement",
         ("caps5", "caps_5", "caps5_change", "caps_5_decrease")),
        ("dissociation_score_change",
         ("dissociation", "des", "des_ii")),
        ("functional_outcome",
         ("functional", "function_score", "wsas")),
        ("suicide_event",
         ("suicide", "suicide_attempt")),
    ],
    "C05_insomnia": [
        ("sleep_efficiency_change",
         ("sleep_efficiency", "se", "se_change", "psqi", "psqi_change",
          "sleep_quality")),
        ("hamd_worsening",
         ("hamd", "depression_worsening", "depression_increase")),
        ("treatment_adherence",
         ("adherence", "app_adherence", "compliance")),
    ],
}

# Flat: any alias / canonical name → canonical name (case- and
# separator-insensitive lookup).
_PRIMARY_OUTCOME_ALIASES: Dict[str, str] = {}
for _cohort, _outcomes in COHORT_PRIMARY_OUTCOMES.items():
    for _canonical, _aliases in _outcomes:
        _PRIMARY_OUTCOME_ALIASES[_canonical] = _canonical
        for _a in _aliases:
            _PRIMARY_OUTCOME_ALIASES[_a] = _canonical


def canonicalize_primary_outcome(value: Any) -> Optional[str]:
    """Canonicalise a free-text ``primary_outcome`` against the registry.

    Returns the canonical name if a match exists in any cohort's
    alias list, otherwise returns the normalised raw string (so the
    LLM can still emit ad-hoc outcomes that aren't in the registry —
    we don't reject them, just don't canonicalise).
    """
    n = _norm(value)
    if not n:
        return None
    if n in _PRIMARY_OUTCOME_ALIASES:
        return _PRIMARY_OUTCOME_ALIASES[n]
    return n  # accept ad-hoc outcomes verbatim (normalised)


def cohort_outcomes(cohort_id: str) -> List[str]:
    """Return the canonical primary-outcome priority list for a cohort.

    Returns an empty list for unknown cohort ids.
    """
    entries = COHORT_PRIMARY_OUTCOMES.get(cohort_id, [])
    return [canonical for canonical, _ in entries]


def cohort_of_outcome(outcome: str) -> Optional[str]:
    """Inverse lookup: given a (canonical or alias) outcome, return
    the cohort id where it sits in the priority list, or None.
    """
    canonical = canonicalize_primary_outcome(outcome)
    if not canonical:
        return None
    for cohort_id, entries in COHORT_PRIMARY_OUTCOMES.items():
        for canonical_name, _aliases in entries:
            if canonical_name == canonical:
                return cohort_id
    return None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _norm(s: Any) -> str:
    """canonicalise enum-ish string: strip / lower / dashes → underscores."""
    if s is None:
        return ""
    return re.sub(r"[\s\-]+", "_", str(s).strip().lower())


def canonicalize_family(value: Any) -> Optional[str]:
    """Return canonical finding_family or None if unrecognised."""
    n = _norm(value)
    if not n:
        return None
    if n in FINDING_FAMILY_VALUES:
        return n
    if n in _FAMILY_ALIASES:
        return _FAMILY_ALIASES[n]
    return None


def canonicalize_modality(value: Any) -> Optional[str]:
    """Return canonical modality enum or None."""
    n = _norm(value)
    if not n:
        return None
    if n in EXPECTED_MODALITY_VALUES:
        return n
    if n in _MODALITY_ALIASES:
        return _MODALITY_ALIASES[n]
    return None


def _coerce_modality_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = [str(raw)]
    out: List[str] = []
    for it in items:
        c = canonicalize_modality(it)
        if c is not None and c not in out:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Hypothesis dataclass
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class Hypothesis:
    """Structured V8 hypothesis card.

    Use :meth:`from_dict` to parse a raw LLM JSON object (with lenient
    enum handling); use :meth:`to_dict` to emit canonical JSON.
    """
    finding_family: str
    expected_hops: int
    expected_agent_workflow_length: int
    expected_modality: List[str] = dataclasses.field(default_factory=list)

    # Optional V7-compatible fields.
    id: str = dataclasses.field(default_factory=lambda: f"H{uuid.uuid4().hex[:8]}")
    variables: List[str] = dataclasses.field(default_factory=list)
    edge_type: Optional[str] = None
    expected_effect_direction: Optional[str] = None
    rationale: Optional[str] = None
    decoy_or_novel: Optional[str] = None
    # G5 (V8 §D.4) — primary outcome the finding measures.  Canonical
    # against COHORT_PRIMARY_OUTCOMES; ad-hoc outcomes are accepted
    # verbatim (just normalised).  Optional — omit for tool tasks.
    primary_outcome: Optional[str] = None
    # G5 — cohort id this outcome belongs to (auto-inferred from the
    # outcome via cohort_of_outcome; None if the outcome isn't in the
    # cohort registry or wasn't given).
    cohort_id: Optional[str] = None

    # Validation diagnostics — populated by from_dict / validate.
    warnings: List[str] = dataclasses.field(default_factory=list)

    # ---------------------- parsing / validation ----------------------
    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Hypothesis":
        """Parse a hypothesis dict from LLM JSON output.

        Lenient on enum spellings (uses ``_FAMILY_ALIASES`` and
        ``_MODALITY_ALIASES``).  Raises ``ValueError`` only on hard
        format errors (missing required fields, hops out of {1,2,3}).
        """
        if not isinstance(raw, dict):
            raise ValueError("hypothesis must be a JSON object")
        warns: List[str] = []

        # finding_family (required)
        fam = canonicalize_family(raw.get("finding_family"))
        if fam is None:
            orig = raw.get("finding_family")
            warns.append(f"unknown finding_family={orig!r}, defaulting to 'other'")
            fam = "other"

        # expected_hops (required, 1..3 — V8 §C.2 forbids 4+)
        h_raw = raw.get("expected_hops")
        try:
            hops = int(h_raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"expected_hops must be int 1/2/3, got {h_raw!r}"
            )
        if hops < MIN_HOPS:
            warns.append(f"expected_hops={hops}<1, clamped to 1")
            hops = MIN_HOPS
        if hops > MAX_HOPS:
            # V8 §C.2: do NOT silently accept; this is a hard rule.
            raise ValueError(
                f"expected_hops={hops} > {MAX_HOPS} not allowed "
                "(V8 §C.2; 4+ hops are out of scope, defer to V9)"
            )

        # expected_agent_workflow_length (required, clamp to 5..30)
        wf_raw = raw.get("expected_agent_workflow_length")
        try:
            wf = int(wf_raw)
        except (TypeError, ValueError):
            raise ValueError(
                "expected_agent_workflow_length must be int 5..30, "
                f"got {wf_raw!r}"
            )
        if wf < MIN_WF_LEN:
            warns.append(
                f"expected_agent_workflow_length={wf} < {MIN_WF_LEN}, "
                f"clamped to {MIN_WF_LEN}"
            )
            wf = MIN_WF_LEN
        if wf > MAX_WF_LEN:
            warns.append(
                f"expected_agent_workflow_length={wf} > {MAX_WF_LEN}, "
                f"clamped to {MAX_WF_LEN}"
            )
            wf = MAX_WF_LEN

        # expected_modality (list[enum], canonicalised + dedup)
        mods = _coerce_modality_list(raw.get("expected_modality"))
        # V8 §C.4 rule: single_snp + prs both present → drop single_snp.
        if "genotype_single_snp" in mods and "genotype_prs" in mods:
            warns.append(
                "modality conflict: both 'genotype_single_snp' and "
                "'genotype_prs' present; dropping single_snp per V8 §C.4 "
                "(PRS preferred)"
            )
            mods = [m for m in mods if m != "genotype_single_snp"]

        # Optional fields — keep loosely validated.
        edge_t_raw = raw.get("edge_type")
        edge_t = _norm(edge_t_raw) if edge_t_raw is not None else None
        if edge_t is not None and edge_t not in EDGE_TYPE_VALUES:
            warns.append(
                f"edge_type={edge_t_raw!r} not in {EDGE_TYPE_VALUES}, "
                "kept as raw"
            )
            edge_t = str(edge_t_raw)

        decoy_raw = raw.get("decoy_or_novel")
        decoy = _norm(decoy_raw) if decoy_raw is not None else None
        if decoy is not None and decoy not in DECOY_VALUES:
            warns.append(
                f"decoy_or_novel={decoy_raw!r} not in {DECOY_VALUES}, "
                "kept as raw"
            )
            decoy = str(decoy_raw)

        variables = raw.get("variables") or []
        if isinstance(variables, str):
            variables = [variables]
        variables = [str(v).strip() for v in variables if str(v).strip()]

        hid = str(raw.get("id") or f"H{uuid.uuid4().hex[:8]}").strip()

        # G5 — primary_outcome (optional) + cohort inference.  Accept
        # ad-hoc outcomes (canonicalize_primary_outcome returns the
        # normalised raw if no alias match) so we don't reject
        # finding studies that don't fit the 5 canonical cohorts.
        po_raw = raw.get("primary_outcome")
        primary_outcome = canonicalize_primary_outcome(po_raw)
        # Caller may also pass an explicit cohort_id; otherwise infer
        # from the canonical outcome.
        cohort_id_raw = raw.get("cohort_id")
        if cohort_id_raw is not None and str(cohort_id_raw).strip():
            cohort_id = str(cohort_id_raw).strip()
        elif primary_outcome is not None:
            cohort_id = cohort_of_outcome(primary_outcome)
        else:
            cohort_id = None

        return cls(
            finding_family=fam,
            expected_hops=hops,
            expected_agent_workflow_length=wf,
            expected_modality=mods,
            id=hid,
            variables=variables,
            edge_type=edge_t,
            expected_effect_direction=(
                str(raw.get("expected_effect_direction")).strip()
                if raw.get("expected_effect_direction") is not None
                else None
            ),
            rationale=(
                str(raw.get("rationale")).strip()
                if raw.get("rationale") is not None
                else None
            ),
            decoy_or_novel=decoy,
            primary_outcome=primary_outcome,
            cohort_id=cohort_id,
            warnings=warns,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        # Drop warnings from canonical serialisation (they are diagnostics).
        d.pop("warnings", None)
        # Drop None optional fields for tidiness.
        for k in ("edge_type", "expected_effect_direction", "rationale",
                   "decoy_or_novel", "primary_outcome", "cohort_id"):
            if d.get(k) is None:
                d.pop(k, None)
        if not d.get("variables"):
            d.pop("variables", None)
        return d

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)


# ---------------------------------------------------------------------------
# Public helper: parse OR return reason
# ---------------------------------------------------------------------------
def try_parse_hypothesis(raw: Any) -> Tuple[Optional[Hypothesis], Optional[str]]:
    """Lenient wrapper.  Returns (hypothesis, None) on success, or
    (None, error_str) on failure.  Use this from the planner so that
    a malformed hypothesis card never crashes the whole pipeline.
    """
    if raw is None:
        return None, None  # absence is fine (e.g. trivial pipeline tasks)
    if not isinstance(raw, dict):
        return None, f"hypothesis must be a dict, got {type(raw).__name__}"
    try:
        return Hypothesis.from_dict(raw), None
    except ValueError as e:
        return None, str(e)


__all__ = [
    "Hypothesis",
    "try_parse_hypothesis",
    "canonicalize_family",
    "canonicalize_modality",
    "FINDING_FAMILY_VALUES",
    "EXPECTED_MODALITY_VALUES",
    "EDGE_TYPE_VALUES",
    "DECOY_VALUES",
    "MIN_HOPS",
    "MAX_HOPS",
    "MIN_WF_LEN",
    "MAX_WF_LEN",
]
