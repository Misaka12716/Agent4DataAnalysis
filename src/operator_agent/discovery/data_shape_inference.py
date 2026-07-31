# -*- coding: utf-8 -*-
"""Schema-aware dataset-shape inference for the discovery framework.

The N2 ``data_processing`` stage produces a per-column profile; that profile is
then handed to the N3 hypothesis-generation agent.  The profiler is dataset-
agnostic: it lists columns with dtypes / ranges / sample values but never says
*what kind of dataset* the user has supplied.  An LLM that only sees column
names can therefore mis-classify the task — for example, given a two-group
differential-expression (DEG) result it will dutifully produce a hypothesis
whose ``variables = ["gene_symbol", "logFC", "adj_p_value"]`` (treating raw
column names as biological entities), which is wrong both for the planner's
operator selection and for the reviewer's interpretation.

This module adds a small heuristic that recognises a handful of common
biomedical dataset shapes (DEG output, parallel-arm intervention, survival,
case-control) and emits a short *banner* the caller can prepend to the
profile text.  The banner explicitly tells the LLM:

  * what kind of table it is;
  * which columns play which role (so it does not need to re-derive that);
  * what hypothesis ``variables`` *should* and *should not* look like for that
    shape (e.g. "use a pathway / gene set / phenotype, NEVER a raw column
    name like 'logFC'").

The inference is fully heuristic — it never raises and degrades gracefully to
``None`` when nothing matches (in which case the original profile text is
used unchanged).  Confidence and matched-column evidence are returned so a
human can audit any suggestion.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

__all__ = [
    "InferredShape",
    "infer_shape",
    "format_shape_banner",
    "with_shape_banner",
]


# --------------------------------------------------------------------------
# Result dataclass
# --------------------------------------------------------------------------
@dataclasses.dataclass
class InferredShape:
    """Result of :func:`infer_shape`.

    ``label`` is a stable machine token (e.g. ``"two_group_deg_result"``);
    ``title`` is a short human title used in the banner header;
    ``description`` is the one-paragraph instruction that is shown to the
    downstream LLM (it should be self-contained — read on its own it must
    make sense to the LLM); ``confidence`` is a coarse ``0..1`` score so a
    consumer can refuse to trust a low-confidence guess; ``evidence`` lists
    ``"role: column_name"`` strings so the inference is auditable.
    """
    label: str
    title: str
    description: str
    confidence: float
    evidence: List[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "title": self.title,
            "description": self.description,
            "confidence": float(self.confidence),
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "InferredShape":
        return cls(
            label=str(d.get("label") or ""),
            title=str(d.get("title") or ""),
            description=str(d.get("description") or ""),
            confidence=float(d.get("confidence") or 0.0),
            evidence=list(d.get("evidence") or []),
        )


# --------------------------------------------------------------------------
# Column-name patterns (case-insensitive, full-match against normalised name)
# --------------------------------------------------------------------------
def _normalise(name: str) -> str:
    """Lower-case, strip surrounding whitespace, collapse separators."""
    s = str(name).strip().lower()
    # treat spaces / hyphens / dots as underscores so "log FC" → "log_fc",
    # "log.FoldChange" → "log_foldchange", etc.
    s = re.sub(r"[\s\-\.]+", "_", s)
    return s


def _match_any(name: str, patterns: Sequence[str]) -> Optional[str]:
    """Return the first pattern that fully matches ``name`` (already
    normalised), or None.  Patterns are anchored automatically."""
    for pat in patterns:
        if re.fullmatch(pat, name):
            return pat
    return None


# --- DEG-result patterns ---------------------------------------------------
_EFFECT_PATTERNS = (
    r"log_?2?_?fold_?change",       # logFoldChange, log2_fold_change
    r"log_?2?fc",                    # logFC, log2FC, log_fc
    r"l2fc",
    r"fold_?change",                 # foldChange, fold_change
    r"effect_?size",                 # effect_size
)
_RAW_P_PATTERNS = (
    r"p_?value",
    r"pval(?:ue)?",
    r"raw_?p(?:_value)?",
    r"p",                            # bare 'p' (DESeq2 etc. use 'pvalue' too)
    r"pr_?gt_?\|t\|",                # Pr(>|t|)
)
_ADJ_P_PATTERNS = (
    r"adj_?p(?:_value)?",
    r"padj",
    r"adjusted_?p(?:_value)?",
    r"q_?value",
    r"qval",
    r"fdr",
    r"bh(?:_p)?",
)
_GENE_PATTERNS = (
    r"gene_?symbol",
    r"gene_?name",
    r"gene_?id",
    r"gene",
    r"ensembl(?:_id|_gene_id)?",
    r"entrez(?:_id|_gene_id)?",
    r"probe_?id",
    r"transcript_?id",
    r"uniprot(?:_id)?",
    r"hgnc(?:_id|_symbol)?",
)
_SAMPLE_N_PATTERNS = (
    r"n_a", r"n_b",
    r"n_case", r"n_ctrl", r"n_control",
    r"n_treat(?:ed|ment)?", r"n_disease", r"n_healthy",
    r"n_group_?\d*",
)


# --- two-arm intervention patterns -----------------------------------------
_ARM_NAME_PATTERNS = (
    r"treatment_?arm", r"arm",
    r"treatment", r"intervention",
    r"group", r"cohort",
    r"randomi[sz]ation", r"allocation",
)
# Lower-cased values that commonly mark arm levels in a binary/few-level col.
_ARM_VALUE_TOKENS = (
    "placebo", "control", "ctrl", "ctl",
    "treatment", "treated", "drug", "active",
    "case", "case_group",
    "intervention", "interventional",
    "arm_a", "arm_b", "arm_1", "arm_2",
    "sham",
)


# --- case-control patterns -------------------------------------------------
_LABEL_NAME_PATTERNS = (
    r"label", r"class", r"target",
    r"diagnosis", r"disease_status",
    r"case_control", r"phenotype",
    r"y", r"y_true",
)
_LABEL_VALUE_TOKENS = (
    "case", "control", "ctrl",
    "disease", "diseased", "healthy", "normal",
    "patient", "ctrl_subject",
    "asd", "autism",
    "tumor", "tumour", "malignant", "benign",
    "0", "1",
)


# --- survival patterns -----------------------------------------------------
_TIME_PATTERNS = (
    r"time", r"duration",
    r"os_?(?:months|days|years|time)?",
    r"pfs_?(?:months|days|years|time)?",
    r"follow_?up(?:_time|_months|_days)?",
    r"time_?to_?event",
    r"survival_?time",
)
_EVENT_PATTERNS = (
    r"event", r"event_?indicator",
    r"status", r"vital_?status",
    r"dead", r"death",
    r"relapse", r"progression",
    r"censor(?:ed|ing)?",
)


# --------------------------------------------------------------------------
# Internal: column scanner
# --------------------------------------------------------------------------
def _scan_columns(df: pd.DataFrame) -> Dict[str, List[str]]:
    """Bucket the dataframe's columns by role, using the pattern lists above.

    Returns a dict ``role -> [original column names]``.  A column may appear
    under multiple roles (e.g. ``"p"`` could match both raw_p and effect for
    a malformed table — we leave the disambiguation to the rule layer).
    """
    roles: Dict[str, List[str]] = {
        "effect": [],
        "raw_p": [],
        "adj_p": [],
        "gene": [],
        "sample_n": [],
        "arm_named": [],
        "label_named": [],
        "time": [],
        "event": [],
    }
    for raw in df.columns:
        norm = _normalise(raw)
        if _match_any(norm, _EFFECT_PATTERNS):
            roles["effect"].append(str(raw))
        if _match_any(norm, _RAW_P_PATTERNS):
            roles["raw_p"].append(str(raw))
        if _match_any(norm, _ADJ_P_PATTERNS):
            roles["adj_p"].append(str(raw))
        if _match_any(norm, _GENE_PATTERNS):
            roles["gene"].append(str(raw))
        if _match_any(norm, _SAMPLE_N_PATTERNS):
            roles["sample_n"].append(str(raw))
        if _match_any(norm, _ARM_NAME_PATTERNS):
            roles["arm_named"].append(str(raw))
        if _match_any(norm, _LABEL_NAME_PATTERNS):
            roles["label_named"].append(str(raw))
        if _match_any(norm, _TIME_PATTERNS):
            roles["time"].append(str(raw))
        if _match_any(norm, _EVENT_PATTERNS):
            roles["event"].append(str(raw))
    return roles


def _safe_value_tokens(df: pd.DataFrame, col: str) -> List[str]:
    """Return up to 10 lower-cased string tokens of the unique values.

    Used to detect arm- or label-style values inside a categorical column
    whose name didn't immediately match (e.g. an unnamed ``group`` col).
    """
    try:
        vc = df[col].dropna().value_counts().head(10)
        return [str(k).strip().lower() for k in vc.index]
    except Exception:
        return []


def _looks_like_two_group_label(df: pd.DataFrame, col: str,
                                 known_tokens: Sequence[str],
                                 *, max_levels: int = 5) -> bool:
    """True if ``col`` is a low-cardinality categorical / binary whose
    values overlap a known token list."""
    try:
        nu = int(df[col].nunique(dropna=True))
    except Exception:
        return False
    if nu < 2 or nu > max_levels:
        return False
    tokens = _safe_value_tokens(df, col)
    if not tokens:
        return False
    return any(any(t.startswith(known) or known in t for known in known_tokens)
               for t in tokens)


# --------------------------------------------------------------------------
# Rule layer: compose one shape from the role buckets
# --------------------------------------------------------------------------
def _try_two_group_deg(df: pd.DataFrame,
                       roles: Dict[str, List[str]]) -> Optional[InferredShape]:
    """Two-group differential-expression result.

    Strong signal = an effect-size column AND a multiplicity-adjusted p-value
    column (raw p alone is borderline).  Optional gene-id / sample-N columns
    add evidence + concrete numbers to the description.
    """
    has_effect = bool(roles["effect"])
    has_raw_p = bool(roles["raw_p"])
    has_adj_p = bool(roles["adj_p"])
    if not has_effect or not (has_raw_p or has_adj_p):
        return None

    evidence: List[str] = []
    confidence = 0.55  # baseline when effect + (raw_p or adj_p)

    if has_effect:
        evidence.append(f"effect: {roles['effect'][0]}")
    if has_raw_p:
        evidence.append(f"raw_p: {roles['raw_p'][0]}")
    if has_adj_p:
        evidence.append(f"adj_p: {roles['adj_p'][0]}")
        confidence += 0.20  # strong DEG-output marker
    if roles["gene"]:
        evidence.append(f"gene: {roles['gene'][0]}")
        confidence += 0.10
    # sample-size columns nail this down — and let us state the design (n vs n)
    n_per_group: List[Tuple[str, int]] = []
    for col in roles["sample_n"]:
        try:
            s = df[col].dropna()
            uniq = sorted({int(v) for v in s.unique() if pd.notna(v)})
            if len(uniq) == 1:
                n_per_group.append((col, uniq[0]))
        except Exception:
            continue
    if n_per_group:
        evidence.append("sample_n: " + ", ".join(
            f"{c}={n}" for c, n in n_per_group))
        confidence += 0.10
    confidence = min(confidence, 0.99)

    # Build a per-row description that names the actual columns we found.
    eff_col = roles["effect"][0]
    rawp = roles["raw_p"][0] if has_raw_p else None
    adjp = roles["adj_p"][0] if has_adj_p else None
    gene = roles["gene"][0] if roles["gene"] else None
    n_clause = ""
    if len(n_per_group) >= 2:
        a, b = n_per_group[0], n_per_group[1]
        n_clause = (f"  Sample sizes per group are explicit: "
                    f"`{a[0]}={a[1]}` vs `{b[0]}={b[1]}`.\n")
    elif len(n_per_group) == 1:
        c, n = n_per_group[0]
        n_clause = f"  Per-group sample size column present: `{c}={n}`.\n"

    p_clause = ""
    if has_adj_p and has_raw_p:
        p_clause = (f"  Each row carries both a raw p-value (`{rawp}`) and a "
                    f"multiplicity-adjusted p-value (`{adjp}`); reviewers "
                    f"will judge significance on `{adjp}`.\n")
    elif has_adj_p:
        p_clause = (f"  Each row carries a multiplicity-adjusted p-value "
                    f"(`{adjp}`); reviewers will judge significance on it.\n")
    elif has_raw_p:
        p_clause = (f"  Each row carries a raw p-value (`{rawp}`) but NO "
                    f"adjusted/FDR column; reviewers will downgrade "
                    f"credibility unless a correction is applied.\n")

    gene_clause = ""
    if gene:
        gene_clause = (f"  Each row identifies one feature via `{gene}` "
                       f"(gene-level granularity).\n")

    description = (
        "This dataset is a two-group differential-expression (DEG) RESULT "
        "table — it is the OUTPUT of an upstream comparison such as limma / "
        "DESeq2 / edgeR, not a raw matrix of samples × features.\n"
        + gene_clause
        + f"  Effect size per row is `{eff_col}` (log2 fold change between "
          "the two groups).\n"
        + p_clause
        + n_clause
        + "Implications for hypothesis generation:\n"
        "  * `variables` MUST describe BIOLOGICAL / CLINICAL entities that "
        "the data can test — e.g. a pathway / gene set name, a phenotype, a "
        "treatment vs control comparison.  NEVER use raw column names "
        "like `"
        + eff_col
        + "` or `"
        + (adjp or rawp or "p_value")
        + "` as `variables`.\n"
        "  * The natural follow-up analyses on a DEG result are pathway / "
        "gene-set enrichment, gene-level interpretation, or biological-"
        "process annotation — NOT another differential-expression test.\n"
        "  * `primary_outcome` should be an interpretable biological end "
        "point (e.g. enrichment of a pathway in dysregulated genes), not a "
        "raw column name."
    )

    return InferredShape(
        label="two_group_deg_result",
        title="Two-group differential-expression result",
        description=description,
        confidence=confidence,
        evidence=evidence,
    )


def _try_parallel_arm_trial(df: pd.DataFrame,
                              roles: Dict[str, List[str]]) -> Optional[InferredShape]:
    """Parallel-arm intervention study (e.g. RCT, treatment-vs-control)."""
    arm_col: Optional[str] = None
    arm_levels: List[str] = []

    # First try arm-named columns (high specificity).
    for col in roles["arm_named"]:
        try:
            nu = int(df[col].nunique(dropna=True))
        except Exception:
            continue
        if 2 <= nu <= 5:
            arm_col = col
            arm_levels = _safe_value_tokens(df, col)
            break

    # Fallback: scan ALL low-cardinality non-numeric columns for arm-like
    # values (handles the case where the column is just called "treatment"
    # but didn't match the regex, or values like "drug_a"/"drug_b").
    if arm_col is None:
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                continue
            if _looks_like_two_group_label(df, col, _ARM_VALUE_TOKENS):
                arm_col = str(col)
                arm_levels = _safe_value_tokens(df, col)
                break
    if arm_col is None:
        return None

    # Need at least one numeric outcome column to be useful for hypotheses.
    numeric_cols = [str(c) for c in df.columns
                    if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric_cols:
        return None

    confidence = 0.6
    evidence = [f"arm: {arm_col} (levels={arm_levels[:5]})"]
    if arm_col in roles["arm_named"]:
        confidence += 0.15
    evidence.append(f"numeric_outcome_candidates: "
                    f"{numeric_cols[:5]}"
                    f"{' (+more)' if len(numeric_cols) > 5 else ''}")

    description = (
        "This dataset looks like a parallel-arm intervention study: column "
        f"`{arm_col}` splits subjects into a small number of arms (sample "
        f"levels: {arm_levels[:5]}), and there are numeric outcome "
        "candidate columns to compare across arms.\n"
        "Implications for hypothesis generation:\n"
        f"  * `variables` should describe the EXPOSURE-vs-OUTCOME claim "
        f"(e.g. \"arm `{arm_col}` affects <numeric_outcome>\"), NOT raw "
        f"column names.\n"
        "  * `primary_outcome` should name a single concrete numeric "
        "outcome column whose between-arm contrast is the test of "
        "interest.\n"
        "  * Natural analyses: between-arm mean / median comparison, "
        "linear / logistic regression of outcome on arm + covariates."
    )

    return InferredShape(
        label="parallel_arm_intervention",
        title="Parallel-arm intervention study",
        description=description,
        confidence=min(confidence, 0.95),
        evidence=evidence,
    )


def _try_case_control(df: pd.DataFrame,
                      roles: Dict[str, List[str]]) -> Optional[InferredShape]:
    """Case-control / labelled observational dataset."""
    label_col: Optional[str] = None
    label_levels: List[str] = []

    for col in roles["label_named"]:
        try:
            nu = int(df[col].nunique(dropna=True))
        except Exception:
            continue
        if 2 <= nu <= 5:
            label_col = col
            label_levels = _safe_value_tokens(df, col)
            break

    if label_col is None:
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                continue
            if _looks_like_two_group_label(df, col, _LABEL_VALUE_TOKENS,
                                             max_levels=4):
                label_col = str(col)
                label_levels = _safe_value_tokens(df, col)
                break
    if label_col is None:
        return None

    numeric_cols = [str(c) for c in df.columns
                    if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric_cols:
        return None

    confidence = 0.5
    if label_col in roles["label_named"]:
        confidence += 0.15
    evidence = [
        f"label: {label_col} (levels={label_levels[:5]})",
        f"covariate_candidates: "
        f"{numeric_cols[:5]}{' (+more)' if len(numeric_cols) > 5 else ''}",
    ]

    description = (
        "This dataset looks like case-control / labelled observational data: "
        f"column `{label_col}` distinguishes a small number of subject "
        f"classes (sample levels: {label_levels[:5]}), and the remaining "
        "numeric columns can be tested for association with that label.\n"
        "Implications for hypothesis generation:\n"
        f"  * `variables` should describe a BIOMARKER-vs-DISEASE / "
        f"COVARIATE-vs-LABEL claim (e.g. \"<numeric covariate> is "
        f"associated with `{label_col}`\"), NOT raw column names.\n"
        f"  * `primary_outcome` should be the categorical label "
        f"(`{label_col}`) when modelling discrimination, OR the numeric "
        "covariate when modelling its level across classes.\n"
        "  * Natural analyses: group-difference test (t-test / Mann-"
        "Whitney), logistic regression of label on covariates, ROC."
    )

    return InferredShape(
        label="case_control_observational",
        title="Case-control / labelled observational data",
        description=description,
        confidence=min(confidence, 0.92),
        evidence=evidence,
    )


def _try_survival(df: pd.DataFrame,
                  roles: Dict[str, List[str]]) -> Optional[InferredShape]:
    """Time-to-event / survival data."""
    if not (roles["time"] and roles["event"]):
        return None

    time_col = roles["time"][0]
    event_col = roles["event"][0]

    # event column should be low-cardinality (binary or {0,1,2})
    try:
        nu = int(df[event_col].nunique(dropna=True))
    except Exception:
        nu = 99
    if nu > 5:
        return None

    confidence = 0.7
    evidence = [f"time: {time_col}", f"event: {event_col} (levels={nu})"]

    description = (
        "This dataset looks like time-to-event / survival data: column "
        f"`{time_col}` is follow-up time and `{event_col}` is the event "
        f"(or censoring) indicator.\n"
        "Implications for hypothesis generation:\n"
        "  * `variables` should describe a COVARIATE-vs-SURVIVAL claim "
        f"(e.g. \"<covariate> shifts hazard of {event_col}\"), NOT raw "
        "column names.\n"
        f"  * `primary_outcome` should be the time-to-event pair "
        f"(`{time_col}` + `{event_col}`).\n"
        "  * Natural analyses: Kaplan-Meier curves, log-rank test, Cox "
        "proportional-hazards regression."
    )

    return InferredShape(
        label="time_to_event_survival",
        title="Time-to-event / survival data",
        description=description,
        confidence=confidence,
        evidence=evidence,
    )


# Order matters: more specific shapes first.  Case-control runs BEFORE
# parallel-arm because the "control" token overlaps both rule's value
# whitelists, and a case-control study (diagnosis: case/control) should
# not be misclassified as a treatment-vs-control RCT.  A genuine RCT has
# either an arm-named column (caught by the name match before fallback)
# or distinct arm-flavored values like placebo / drug_a / drug_b that the
# case-control rule's _LABEL_VALUE_TOKENS does not match.
_RULES = (
    _try_two_group_deg,
    _try_survival,
    _try_case_control,
    _try_parallel_arm_trial,
)


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------
def infer_shape(df: pd.DataFrame) -> Optional[InferredShape]:
    """Best-effort one-shot inference; returns the first matching shape.

    Returns ``None`` if no rule fires (the caller should keep the original
    profile text unchanged).  Never raises — every rule is wrapped so a bad
    column or unexpected dtype cannot break the data-processing stage.
    """
    if df is None:
        return None
    try:
        roles = _scan_columns(df)
    except Exception:
        return None
    for rule in _RULES:
        try:
            result = rule(df, roles)
        except Exception:
            continue
        if result is not None:
            return result
    return None


def format_shape_banner(shape: InferredShape) -> str:
    """Render an :class:`InferredShape` as a multi-line banner ready to be
    prepended to a profile-text block.

    The banner is delimited by two ``--- ... ---`` rules so a downstream LLM
    can clearly see where the inferred guidance ends and the raw profile
    begins.
    """
    lines = [
        f"--- INFERRED DATASET SHAPE: {shape.title} "
        f"(confidence={shape.confidence:.2f}) ---",
        shape.description.rstrip(),
    ]
    if shape.evidence:
        lines.append("Evidence (auto-detected role → column):")
        for ev in shape.evidence:
            lines.append(f"  - {ev}")
    lines.append("--- end inferred shape; raw column profile follows ---")
    return "\n".join(lines)


def with_shape_banner(profile_text: str,
                       shape: Optional[InferredShape]) -> str:
    """Prepend a banner for ``shape`` (if any) to ``profile_text``.

    Returns ``profile_text`` unchanged when ``shape`` is None — so the
    caller can write::

        text = with_shape_banner(profile_to_text(profile), infer_shape(df))

    without an explicit None-check.
    """
    if shape is None:
        return profile_text
    return format_shape_banner(shape) + "\n\n" + profile_text
