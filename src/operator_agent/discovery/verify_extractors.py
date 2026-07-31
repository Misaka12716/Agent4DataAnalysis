# -*- coding: utf-8 -*-
"""Per-operator evidence extractors used by verify_stage (ADR-0008).

verify_stage scans every executed pipeline step.  For each step it looks
up the step's ``solver`` id in :data:`OPERATOR_EXTRACTORS`.  If found,
the registered extractor knows which keys / which CSV columns / which
output file mean which thing for *that* operator (e.g. for
``limma_deg_two_group``, ``adj.P.Val`` is BH-adjusted while ``P.Value``
is raw — both should be reported with distinct ``p_kind`` labels).

If the operator id is NOT in the table, a fallback extractor walks the
operator's outputs with the existing flat ``_EFFECT_KEYS`` /
``_P_KEYS`` / ``_N_KEYS`` lookup.  This preserves the previous
behaviour for non-bio operators while still producing **one Evidence
per operator** instead of merging across operators.

The flat-scan ``_KEYS`` constants live in :mod:`verify_stage` (we
import them here to avoid duplication).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .types import Evidence

__all__ = [
    "OPERATOR_EXTRACTORS",
    "extract_for_step",
]

# Keys we recognise as the "primary id" column in DEG-table CSVs.
# We don't use this to extract effect/p; just to drive "does the row
# look like a single-row summary or a per-gene table?".
_DEG_ID_COLS = ("gene_symbol", "gene", "probe_id", "id", "name")


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _to_int(v: Any) -> Optional[int]:
    f = _to_float(v)
    if f is None:
        return None
    try:
        return int(f)
    except (TypeError, ValueError):
        return None


def _read_csv_safe(path: Path, nrows: int = 5000) -> Optional[pd.DataFrame]:
    try:
        return pd.read_csv(path, nrows=nrows)
    except Exception:
        return None


def _scalar_outputs(step: Dict[str, Any]) -> Dict[str, Any]:
    out = step.get("outputs") or {}
    return {k: v for k, v in out.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _step_csvs(step: Dict[str, Any]) -> List[Path]:
    out = step.get("outputs") or {}
    paths: List[Path] = []
    for v in out.values():
        if not isinstance(v, str) or not v:
            continue
        p = Path(v)
        try:
            if p.is_file() and p.suffix.lower() == ".csv":
                paths.append(p)
        except Exception:
            continue
    return paths


def _smallest_p_row(df: pd.DataFrame, p_col: str) -> Optional[pd.Series]:
    try:
        ps = pd.to_numeric(df[p_col], errors="coerce")
        if ps.dropna().empty:
            return None
        idx = ps.idxmin()
        return df.loc[idx]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Concrete bio-operator extractors
# ---------------------------------------------------------------------------
def _extract_limma_deg(step: Dict[str, Any], solver_id: str) -> Evidence:
    """``limma_deg_two_group`` → DEG table with logFC + raw P + adj P.

    The step's ``deg_table_csv`` (or whatever ``.csv`` output the step
    yielded) is a per-probe / per-gene table.  As "summary evidence"
    for the hypothesis, we report the **most significant single row**
    (smallest ``adj_p_value`` if available, else smallest ``p_value``)
    along with its ``logFC``.  Sample size comes from per-group
    ``n_a`` / ``n_b`` columns (we report ``min(n_a, n_b) * 2`` style as
    ``n_total`` if available, else max).
    """
    ev = Evidence(source_operator=solver_id, raw=dict(step.get("outputs") or {}))
    csvs = _step_csvs(step)
    target = None
    for p in csvs:
        nm = p.name.lower()
        if "deg" in nm and "table" in nm:
            target = p
            break
    if target is None and csvs:
        target = csvs[0]
    if target is None:
        return ev
    df = _read_csv_safe(target)
    if df is None or df.empty:
        return ev
    cols_lower = {c.lower(): c for c in df.columns}
    p_col = (cols_lower.get("adj_p_value")
             or cols_lower.get("adj.p.val")
             or cols_lower.get("padj")
             or cols_lower.get("p_value")
             or cols_lower.get("p.value")
             or cols_lower.get("pvalue"))
    if p_col is None:
        return ev
    p_kind = "adj_p" if p_col.lower() in (
        "adj_p_value", "adj.p.val", "padj") else "raw_p"
    row = _smallest_p_row(df, p_col)
    if row is None:
        return ev
    ev.p = _to_float(row.get(p_col))
    ev.p_kind = p_kind
    fc_col = (cols_lower.get("logfc")
              or cols_lower.get("log2fc")
              or cols_lower.get("log_fc"))
    if fc_col is not None:
        ev.effect = _to_float(row.get(fc_col))
        ev.effect_kind = "logFC"
    n_a = _to_int(row.get(cols_lower.get("n_a", "")))
    n_b = _to_int(row.get(cols_lower.get("n_b", "")))
    if n_a is not None and n_b is not None:
        ev.n = int(n_a) + int(n_b)
        ev.n_kind = "n_total"
    elif n_a is not None or n_b is not None:
        ev.n = int(n_a or n_b)
        ev.n_kind = "n_per_group"
    else:
        # Fallback: row count of DEG table is the # of features tested.
        ev.n = int(len(df))
        ev.n_kind = "n_features_tested"
    return ev


def _extract_pathway_fisher(step: Dict[str, Any], solver_id: str) -> Evidence:
    """``pathway_enrichment_fisher`` → enrichment table per pathway.

    Similar shape: pick the most significant pathway row (smallest
    ``adj_pvalue`` else ``pvalue``); report ``fold_enrichment`` and
    ``n_overlap`` (genes hitting that pathway).
    """
    ev = Evidence(source_operator=solver_id, raw=dict(step.get("outputs") or {}))
    csvs = _step_csvs(step)
    target = None
    for p in csvs:
        nm = p.name.lower()
        if "enrich" in nm or "pathway" in nm:
            target = p
            break
    if target is None and csvs:
        target = csvs[0]
    if target is None:
        return ev
    df = _read_csv_safe(target)
    if df is None or df.empty:
        return ev
    cols_lower = {c.lower(): c for c in df.columns}
    p_col = (cols_lower.get("adj_pvalue")
             or cols_lower.get("adj_p_value")
             or cols_lower.get("padj")
             or cols_lower.get("pvalue")
             or cols_lower.get("p_value"))
    if p_col is None:
        return ev
    p_kind = "adj_p" if p_col.lower() in (
        "adj_pvalue", "adj_p_value", "padj") else "raw_p"
    row = _smallest_p_row(df, p_col)
    if row is None:
        return ev
    ev.p = _to_float(row.get(p_col))
    ev.p_kind = p_kind
    fe_col = (cols_lower.get("fold_enrichment")
              or cols_lower.get("enrichment_ratio"))
    if fe_col is not None:
        ev.effect = _to_float(row.get(fe_col))
        ev.effect_kind = "fold_enrichment"
    n_overlap_col = (cols_lower.get("n_overlap")
                     or cols_lower.get("overlap"))
    if n_overlap_col is not None:
        ev.n = _to_int(row.get(n_overlap_col))
        ev.n_kind = "n_overlap"
    else:
        ev.n = int(len(df))
        ev.n_kind = "n_pathways_tested"
    return ev


def _extract_probe_collapse(step: Dict[str, Any], solver_id: str) -> Evidence:
    """``probe_deg_collapse_to_gene`` is a transformation step (probe →
    gene).  No new effect / p numbers; we just record that it ran and
    let downstream operators (limma collapse output, pathway) carry
    the actual evidence.
    """
    ev = Evidence(source_operator=solver_id, raw=dict(step.get("outputs") or {}))
    csvs = _step_csvs(step)
    if csvs:
        df = _read_csv_safe(csvs[0])
        if df is not None:
            ev.n = int(len(df))
            ev.n_kind = "n_genes"
    return ev


# ---------------------------------------------------------------------------
# Fallback extractor (unknown / non-bio operators)
# ---------------------------------------------------------------------------
def _fallback_extractor(step: Dict[str, Any], solver_id: str,
                        *,
                        p_keys, effect_keys, n_keys) -> Evidence:
    """Best-effort flat-scan for operators not in the explicit table.

    Looks at scalar outputs first, then the first CSV produced.  Picks
    first-match-wins per-field WITHIN this single operator's outputs.
    Critically: the scan never crosses operator boundaries — that was
    the bug ADR-0008 fixes.
    """
    ev = Evidence(source_operator=solver_id, raw=dict(step.get("outputs") or {}))
    scalars = _scalar_outputs(step)

    def _norm(k: Any) -> str:
        return str(k).strip().lower().replace("-", "_").replace(" ", "_")

    def _scan(mapping: Dict[str, Any]) -> None:
        for k, v in mapping.items():
            nk = _norm(k)
            fv = _to_float(v)
            if fv is None:
                continue
            if ev.p is None and nk in p_keys:
                ev.p = fv
                ev.p_kind = "raw_p"
            elif ev.effect is None and nk in effect_keys:
                ev.effect = fv
                ev.effect_kind = effect_keys[nk]
            elif ev.n is None and nk in n_keys:
                ev.n = int(fv)
                ev.n_kind = nk

    _scan(scalars)
    if ev.effect is None and ev.p is None:
        for csv_path in _step_csvs(step):
            df = _read_csv_safe(csv_path, nrows=200)
            if df is None or df.empty:
                continue
            col_first: Dict[str, Any] = {}
            for col in df.columns:
                series = df[col].dropna()
                if not series.empty:
                    col_first[col] = series.iloc[0]
            _scan(col_first)
            if ev.effect is not None or ev.p is not None:
                break
    return ev


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
OPERATOR_EXTRACTORS: Dict[str, Callable[[Dict[str, Any], str], Evidence]] = {
    "limma_deg_two_group":          _extract_limma_deg,
    "deseq2":                       _extract_limma_deg,   # same shape
    "edger":                        _extract_limma_deg,   # same shape
    "pathway_enrichment_fisher":    _extract_pathway_fisher,
    "pathway_enrichment_ora":       _extract_pathway_fisher,  # same columns
    "pathway_enrichment_gsea":      _extract_pathway_fisher,  # similar columns
    "probe_deg_collapse_to_gene":   _extract_probe_collapse,
}


def extract_for_step(step: Dict[str, Any],
                     *,
                     fallback_p_keys,
                     fallback_effect_keys,
                     fallback_n_keys) -> Evidence:
    """Return one :class:`Evidence` for one executed pipeline step.

    Picks the registered extractor for the step's ``solver`` id; falls
    back to the flat-scan with the supplied keyword sets if no
    extractor is registered.
    """
    solver_id = str(step.get("solver") or step.get("name") or "?")
    extractor = OPERATOR_EXTRACTORS.get(solver_id)
    if extractor is not None:
        try:
            ev = extractor(step, solver_id)
            return ev
        except Exception:
            # Defensive: a buggy extractor must not crash verify_stage.
            # Fall through to the generic scanner.
            pass
    return _fallback_extractor(step, solver_id,
                               p_keys=fallback_p_keys,
                               effect_keys=fallback_effect_keys,
                               n_keys=fallback_n_keys)
