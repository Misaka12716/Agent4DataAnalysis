"""Bulk RNA-seq / microarray differential expression on a sample × gene
table (single-table input, sample_id + binary group + gene-expression
columns).

This is the *V8.2 B1* operator — a thin, **single-table** front-end on
top of two well-known backends, chosen by ``method``:

* ``method="deseq2"`` (PyDESeq2 0.5; Wald test on the negative-binomial
  GLM; the same model used by R's DESeq2).  Requires integer counts.
* ``method="welch_t"`` (default; Welch's two-sample t-test on
  ``log2(x+1)`` — equivalent to the limma-trend baseline for
  microarray / pre-normalised data).  Works on any non-negative numeric
  table and is the most generally applicable option, which is why it is
  the default.
* ``method="auto"`` picks ``deseq2`` if the entire numeric block is
  non-negative integer counts and the smallest group has ≥ 3 samples,
  else ``welch_t``.

The output columns are aligned across both methods so downstream
operators (B2 ORA, B3 GSEA preranked) can consume the same schema
regardless of the chosen backend.

References
----------
* Love MI, Huber W, Anders S (2014) "Moderated estimation of fold
  change and dispersion for RNA-seq data with DESeq2" *Genome Biology*
  15:550.
* Smyth GK (2004) "Linear models and empirical bayes methods for
  assessing differential expression in microarray experiments"
  *Stat Appl Genet Mol Biol* 3:Article 3.
* Ritchie ME et al. (2015) "limma powers differential expression
  analyses for RNA-sequencing and microarray studies" *NAR* 43(7):e47.

Outputs
-------
``de_table.csv``
    one row per gene: ``gene_id, log2FoldChange, baseMean, stat,
    p_value, adj_p_value, n_control, n_treated``
``de_summary.json``
    {n_genes_total, n_significant_fdr_0.05, top_up_5, top_down_5,
    method, group_control, group_treated, n_samples_control,
    n_samples_treated}
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as sps

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract


CONTRACT = SolverContract(
    name="differential_expression_limma",
    capability="F_bio_differential_expression",
    description=(
        "Per-gene differential expression on a sample × gene table "
        "with a binary group column.  ``method='deseq2'`` uses "
        "PyDESeq2 NB-Wald (requires integer counts; matches R DESeq2). "
        "``method='welch_t'`` (default) uses Welch t-test on "
        "log2(x+1) — equivalent to a limma-trend / vanilla two-sample "
        "test, works on microarray and pre-normalised data.  "
        "``method='auto'`` picks deseq2 for integer counts, welch_t "
        "otherwise.  Output schema is uniform across methods so it "
        "can be consumed by ORA / GSEA operators downstream."
    ),
    roles={
        "sample_id_col": RoleSpec(
            Role.ID,
            "Sample identifier column.  One row per sample.",
            optional=True,  # if absent, runner-injected __row_id__ is used
        ),
        "group_col": RoleSpec(
            Role.BINARY_TARGET,
            "Two-level grouping column (e.g. 'control' / 'treated', "
            "0 / 1, 'A' / 'B').  Alphabetically first level becomes "
            "the reference (denominator of log2 fold change).",
        ),
    },
    static_params={
        "method": "welch_t",     # "deseq2" | "welch_t" | "auto"
        "alpha": 0.05,
        "lfc_min_for_summary": 1.0,
    },
    output_files={
        "de_table_csv":   "de_table.csv",
        "de_summary_json": "de_summary.json",
    },
    output_kind={"de_table_csv": "s", "de_summary_json": "s"},
)


def _unwrap_mapping_value(v: Any) -> Any:
    """Unwrap ``{'<name>': value}`` → ``value`` for single-key dicts.

    The LLM mapping engine occasionally emits ``{"sample_id_col": "id"}``
    instead of plain ``"id"`` for PARAMS / SCALAR roles whose name
    matches the contract ``static_params`` key.  This makes the solver
    tolerant to that nesting.
    """
    if isinstance(v, dict) and len(v) == 1:
        only = next(iter(v.values()))
        return _unwrap_mapping_value(only)
    return v


def _bh_fdr(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg FDR (NaN-safe vectorised)."""
    p = np.asarray(p, dtype=float)
    mask = np.isfinite(p)
    adj = np.full_like(p, np.nan, dtype=float)
    if not mask.any():
        return adj
    pp = p[mask]
    n = len(pp)
    order = np.argsort(pp)
    ranked = pp[order]
    raw = ranked * (n / np.arange(1, n + 1))
    adj_sorted = np.minimum.accumulate(raw[::-1])[::-1]
    adj_sorted = np.minimum(adj_sorted, 1.0)
    sub_adj = np.empty(n, dtype=float)
    sub_adj[order] = adj_sorted
    adj[mask] = sub_adj
    return adj


def _detect_integer_counts(X: np.ndarray) -> bool:
    """Return True iff X looks like integer count data."""
    finite = X[np.isfinite(X)]
    if finite.size == 0:
        return False
    if (finite < 0).any():
        return False
    return bool(np.all(np.isclose(finite, np.rint(finite), atol=1e-6)))


def _gene_cols(df: pd.DataFrame, sample_id_col: Optional[str],
                group_col: str) -> List[str]:
    """All numeric columns *except* the sample_id / group / row_id helpers."""
    skip = {group_col, "__row_id__"}
    if sample_id_col:
        skip.add(sample_id_col)
    cols: List[str] = []
    for c in df.columns:
        if c in skip:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols


def _coerce_continuous(s: pd.Series) -> pd.Series:
    """Numeric-coerce a continuous trait column.  Strings like
    ``"1.55"``, ``"  1.55 cm"``, ``"1,55"`` (European decimal) all
    get coerced to ``float`` where possible; non-coercable cells
    become ``NaN``.
    """
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    sstr = s.astype(str).str.strip()
    cleaned = sstr.str.replace(r"\s*([a-zA-Z%]+)\s*$", "", regex=True)
    mask_eu = (cleaned.str.count(",") == 1) & (~cleaned.str.contains(r"\."))
    cleaned = cleaned.where(~mask_eu, cleaned.str.replace(",", ".", regex=False))
    return pd.to_numeric(cleaned, errors="coerce")


def _classify_trait(s: pd.Series) -> str:
    """Return one of ``binary`` / ``categorical`` / ``continuous``.

    Decision rule (chosen to match how a biostatistician would route):

    * If after string-level normalisation the column has exactly 2
      unique non-null values → ``binary`` (regardless of whether
      they're stored as strings, ints, floats, or "yes"/"no").
    * If the column has ≥3 unique non-null values AND ≥80 % of them
      can be coerced to numeric (including messy formats like
      ``"1.55 cm"`` or European decimal ``"1,55"``) AND its numeric
      values have ≥3 distinct levels → ``continuous`` (e.g. Height
      in cm, Age in years).
    * Otherwise → ``categorical`` (e.g. 3-level disease grade); we
      reject it from this solver because it requires multinomial /
      ANOVA-style modelling that is not the V8.2 DE scope.
    """
    sclean = s.dropna()
    if len(sclean) == 0:
        return "categorical"
    sstr = sclean.astype(str).str.strip()
    uniq_str = [u for u in sstr.unique().tolist()
                  if u not in ("", "nan", "None", "NA", "<NA>")]
    if len(uniq_str) == 2:
        return "binary"
    # Try messy-aware numeric coercion (matches _coerce_continuous so
    # detection and routing agree on the same set of "numeric" rows).
    coerced = _coerce_continuous(sclean)
    n_numeric = int(coerced.notna().sum())
    if n_numeric >= 0.8 * len(sclean) and coerced.dropna().nunique() >= 3:
        return "continuous"
    return "categorical"


def _binarise_group(s: pd.Series) -> Tuple[pd.Series, str, str]:
    """Return (binary series 0/1, ref_label, alt_label).

    ref = alphabetically/numerically smallest of the two levels.
    Handles numeric (0/1, 1.0/2.0, ...) and string ("yes"/"no",
    "M"/"F", "case"/"control") encodings.
    """
    sclean = s.dropna()
    # try strict numeric first
    snum = pd.to_numeric(sclean, errors="coerce")
    if snum.notna().sum() == len(sclean) and snum.nunique() == 2:
        levels = sorted(snum.unique().tolist())
        ref, alt = levels[0], levels[1]
        out = pd.to_numeric(s, errors="coerce")
        out = (out == alt).astype(float)
        out[pd.to_numeric(s, errors="coerce").isna()] = np.nan
        return out, str(ref), str(alt)
    # generic factorize on string repr
    sstr = s.astype(str).str.strip()
    uniq = sorted([u for u in sstr.unique().tolist()
                     if u not in ("", "nan", "None", "NA", "<NA>")])
    if len(uniq) != 2:
        raise ValueError(
            f"differential_expression_limma: group_col must have exactly 2 "
            f"non-null levels; got {len(uniq)}: {uniq[:8]}")
    ref, alt = uniq[0], uniq[1]
    out = pd.Series(np.where(sstr == alt, 1.0,
                              np.where(sstr == ref, 0.0, np.nan)),
                     index=s.index)
    return out, ref, alt


def _run_continuous_correlation(
    expr: pd.DataFrame, trait_vals: pd.Series,
    method: str = "spearman",
) -> pd.DataFrame:
    """Per-gene correlation with a continuous trait.

    Uses Spearman ρ by default (robust to non-linearity and outliers,
    matches what biostatisticians do when DE is asked of a continuous
    phenotype).  Falls back to Pearson if explicitly requested.

    Returns a DataFrame in the same schema as ``_run_welch_t`` so
    the rest of the pipeline (BH correction, CSV / JSON output, the
    Coder's reading code, ORA's ``deg_table_csv`` consumer) keeps
    working unchanged.

    Column meanings (re-used so the schema stays stable):

    * ``log2FoldChange`` → correlation coefficient (range [-1, 1]).
      This *is* an effect-size column; downstream "high |log2FC|" /
      "high |effect|" filters still make sense, just with a
      smaller-magnitude scale.
    * ``stat``           → t-statistic of the correlation
      ``ρ * sqrt((n-2) / (1-ρ²))``.
    * ``p_value``        → two-sided p of ``stat`` under t_{n-2}.
    * ``n_control`` / ``n_treated`` → both set to total n (no group
      split in continuous mode).
    """
    X = expr.to_numpy(dtype=float)
    y = trait_vals.to_numpy(dtype=float)
    # Drop rows where y is NaN (cell-level missingness in X is
    # handled per-gene below).
    keep = np.isfinite(y)
    X = X[keep]
    y = y[keep]
    n_samples = X.shape[0]
    if n_samples < 4:
        raise ValueError(
            f"continuous-trait DE needs ≥4 samples with finite trait "
            f"value; got {n_samples}")
    method = (method or "spearman").lower()
    if method == "spearman":
        # Rank within column, then Pearson on the ranks.
        # We do this manually (no scipy.spearmanr loop) for speed.
        Xr = pd.DataFrame(X).rank(method="average", na_option="keep").to_numpy()
        yr = pd.Series(y).rank(method="average").to_numpy()
    elif method == "pearson":
        Xr, yr = X, y
    else:
        raise ValueError(
            f"_run_continuous_correlation: method={method!r} not in "
            "{spearman, pearson}")
    # Pearson correlation per column
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        Xc = Xr - np.nanmean(Xr, axis=0, keepdims=True)
        yc = yr - np.nanmean(yr)
        num = np.nansum(Xc * yc[:, None], axis=0)
        dX = np.sqrt(np.nansum(Xc ** 2, axis=0))
        dy = np.sqrt(np.nansum(yc ** 2))
        rho = np.where((dX > 0) & (dy > 0), num / (dX * dy), np.nan)
    # Effective n per gene (drop NaN expressions)
    n_eff = (~np.isnan(X)).sum(axis=0)
    # t = rho * sqrt((n-2) / (1 - rho^2)); guard rho^2 ≥ 1
    rho2 = np.clip(rho ** 2, 0.0, 1.0 - 1e-12)
    df_t = np.where(n_eff > 2, n_eff - 2, np.nan)
    t = np.where(df_t > 0, rho * np.sqrt(df_t / (1.0 - rho2)), np.nan)
    p = np.full_like(t, np.nan, dtype=float)
    valid = np.isfinite(t) & np.isfinite(df_t)
    p[valid] = 2.0 * sps.t.sf(np.abs(t[valid]), df_t[valid])
    baseMean = np.nanmean(X, axis=0)
    return pd.DataFrame({
        "gene_id":         expr.columns,
        "log2FoldChange":  rho,            # effect-size = correlation
        "baseMean":        baseMean,
        "stat":            t,
        "p_value":         p,
        "n_control":       n_samples * np.ones(len(expr.columns), dtype=int),
        "n_treated":       n_samples * np.ones(len(expr.columns), dtype=int),
    })


def _run_welch_t(
    expr: pd.DataFrame, group_bin: pd.Series, ref: str, alt: str
) -> pd.DataFrame:
    """Welch t-test per gene on log2(x+1).  Vectorised."""
    A = expr[group_bin == 0].to_numpy(dtype=float)
    B = expr[group_bin == 1].to_numpy(dtype=float)
    if A.shape[0] < 2 or B.shape[0] < 2:
        raise ValueError(
            f"welch_t needs ≥2 samples per group; got "
            f"n({ref})={A.shape[0]}, n({alt})={B.shape[0]}")
    A = np.log2(A + 1.0)
    B = np.log2(B + 1.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mA = np.nanmean(A, axis=0)
        mB = np.nanmean(B, axis=0)
        vA = np.nanvar(A, axis=0, ddof=1)
        vB = np.nanvar(B, axis=0, ddof=1)
        nA = np.sum(~np.isnan(A), axis=0)
        nB = np.sum(~np.isnan(B), axis=0)
    se = np.sqrt(vA / nA + vB / nB)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (mB - mA) / se
        # Welch-Satterthwaite df
        denom = ((vA / nA) ** 2 / (nA - 1) +
                  (vB / nB) ** 2 / (nB - 1))
        df_ws = ((vA / nA + vB / nB) ** 2 / denom)
    valid = np.isfinite(t) & np.isfinite(df_ws) & (df_ws > 0)
    p = np.full_like(t, np.nan, dtype=float)
    p[valid] = 2.0 * sps.t.sf(np.abs(t[valid]), df_ws[valid])
    logFC = mB - mA   # already on log2 scale because of log2(x+1)
    baseMean = np.nanmean(np.vstack([A, B]), axis=0)
    return pd.DataFrame({
        "gene_id":         expr.columns,
        "log2FoldChange":  logFC,
        "baseMean":        baseMean,
        "stat":            t,
        "p_value":         p,
        "n_control":       int(A.shape[0]) * np.ones(len(expr.columns), dtype=int),
        "n_treated":       int(B.shape[0]) * np.ones(len(expr.columns), dtype=int),
    })


def _run_deseq2(
    expr: pd.DataFrame, group_bin: pd.Series, ref: str, alt: str
) -> pd.DataFrame:
    """PyDESeq2 NB-Wald per gene.  Requires integer counts."""
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.ds import DeseqStats

    counts = expr.astype(int).copy()
    # PyDESeq2 wants integer-typed values; ensure non-negative.
    if (counts.to_numpy() < 0).any():
        raise ValueError("deseq2 requires non-negative integer counts.")
    metadata = pd.DataFrame({
        "condition": np.where(group_bin == 1, "treated", "control"),
    }, index=counts.index)
    dds = DeseqDataSet(
        counts=counts, metadata=metadata,
        design="~condition",
        ref_level=["condition", "control"],
        quiet=True,
        n_cpus=1,
    )
    dds.deseq2()
    stats = DeseqStats(
        dds, contrast=["condition", "treated", "control"],
        quiet=True,
    )
    stats.summary()
    res = stats.results_df.reset_index().rename(columns={"index": "gene_id"})
    # Align schema
    res = res.rename(columns={"pvalue": "p_value", "padj": "_padj_dropped"})
    res = res[["gene_id", "log2FoldChange", "baseMean",
                "stat", "p_value"]].copy()
    n_ctrl = int((group_bin == 0).sum())
    n_trt = int((group_bin == 1).sum())
    res["n_control"] = n_ctrl
    res["n_treated"] = n_trt
    return res


class DifferentialExpressionLimmaSolver:
    contract = CONTRACT

    def __init__(self, method: str = "welch_t", alpha: float = 0.05,
                  lfc_min_for_summary: float = 1.0):
        self.method = str(method).lower()
        self.alpha = float(alpha)
        self.lfc_min_for_summary = float(lfc_min_for_summary)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        sample_id_col = _unwrap_mapping_value(mapping.get("sample_id_col"))
        group_col = _unwrap_mapping_value(mapping.get("group_col"))
        method = _unwrap_mapping_value(mapping.get("method"))
        method = str(method or self.method or "welch_t").lower()
        if group_col is None or group_col not in df.columns:
            raise KeyError(
                f"differential_expression_limma: group_col {group_col!r} "
                "missing in DataFrame")

        gene_cols = _gene_cols(df, sample_id_col, group_col)
        if len(gene_cols) < 5:
            raise ValueError(
                f"differential_expression_limma: need ≥5 gene columns, "
                f"got {len(gene_cols)} numeric columns after excluding "
                f"id/group")

        expr = df[gene_cols].copy()
        expr = expr.apply(pd.to_numeric, errors="coerce")

        # ---- trait type detection + dispatch ------------------------
        trait_kind = _classify_trait(df[group_col])
        if trait_kind == "categorical":
            n_levels = (df[group_col].dropna().astype(str).str.strip()
                          .nunique())
            raise ValueError(
                f"differential_expression_limma: group_col "
                f"{group_col!r} has {n_levels} non-numeric / discrete "
                "levels; this solver supports binary (2 levels) or "
                "continuous (≥3 numeric levels) traits.  For "
                "multi-class ANOVA contrasts, please pre-split into "
                "pair-wise comparisons.")

        if trait_kind == "binary":
            gb, ref, alt = _binarise_group(df[group_col])
            keep = gb.notna()
            if int(keep.sum()) < 4:
                raise ValueError(
                    f"differential_expression_limma: only "
                    f"{int(keep.sum())} rows have a valid group "
                    "label (need ≥4)")
            expr = expr.loc[keep].reset_index(drop=True)
            gb = gb.loc[keep].reset_index(drop=True)

            if method == "auto":
                method = ("deseq2"
                           if (_detect_integer_counts(expr.to_numpy())
                                and (gb == 0).sum() >= 3
                                and (gb == 1).sum() >= 3)
                           else "welch_t")
            if method == "deseq2":
                res = _run_deseq2(expr, gb, ref, alt)
            elif method == "welch_t":
                res = _run_welch_t(expr, gb, ref, alt)
            elif method in {"spearman", "pearson"}:
                # User explicitly asks for a continuous-style test on
                # a binary trait — biserial / point-biserial.  We
                # honour it by running correlation against 0/1.
                res = _run_continuous_correlation(expr, gb, method=method)
                ref, alt = ref, alt  # keep labels for summary
            else:
                raise ValueError(
                    f"differential_expression_limma: unknown method "
                    f"{method!r}; choose deseq2 | welch_t | auto | "
                    "spearman | pearson")
        else:  # continuous
            trait_vals = _coerce_continuous(df[group_col])
            keep = trait_vals.notna()
            if int(keep.sum()) < 4:
                raise ValueError(
                    f"differential_expression_limma: only "
                    f"{int(keep.sum())} samples have a valid "
                    "continuous trait value (need ≥4)")
            expr = expr.loc[keep].reset_index(drop=True)
            trait_vals = trait_vals.loc[keep].reset_index(drop=True)
            if method in {"auto", "welch_t", "deseq2"}:
                # Auto-route binary methods to spearman for continuous
                method = "spearman"
            if method not in {"spearman", "pearson"}:
                raise ValueError(
                    f"differential_expression_limma: continuous trait "
                    f"requires method ∈ {{spearman, pearson, auto}}; "
                    f"got {method!r}")
            res = _run_continuous_correlation(expr, trait_vals,
                                                 method=method)
            # for summary: synthesise pseudo ref/alt labels
            qlo, qhi = trait_vals.quantile([0.25, 0.75]).tolist()
            ref = f"low_q25={qlo:.3g}"
            alt = f"high_q75={qhi:.3g}"
            gb = pd.Series(np.nan, index=trait_vals.index)
        res["adj_p_value"] = _bh_fdr(res["p_value"].to_numpy())
        res = res.sort_values(["adj_p_value", "p_value"],
                                kind="mergesort",
                                na_position="last").reset_index(drop=True)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        de_path = out_dir / CONTRACT.output_files["de_table_csv"]
        res.to_csv(de_path, index=False)

        # Summary JSON
        finite = res.dropna(subset=["p_value"])
        n_sig = int((finite["adj_p_value"] < self.alpha).sum()
                     if "adj_p_value" in finite.columns else 0)
        sig = finite[(finite["adj_p_value"] < self.alpha)
                       & (finite["log2FoldChange"].abs()
                           >= self.lfc_min_for_summary)]
        top_up = (sig.sort_values("log2FoldChange", ascending=False)
                    .head(5)["gene_id"].tolist())
        top_down = (sig.sort_values("log2FoldChange", ascending=True)
                      .head(5)["gene_id"].tolist())
        n_ctrl = int((gb == 0).sum()) if gb.notna().any() else 0
        n_trt = int((gb == 1).sum()) if gb.notna().any() else 0
        summary = {
            "method":                method,
            "trait_kind":            trait_kind,
            "group_control":         ref,
            "group_treated":         alt,
            "n_samples_control":     n_ctrl,
            "n_samples_treated":     n_trt,
            "n_samples_total":       int(len(expr)),
            "n_genes_total":         int(len(res)),
            "n_significant_fdr_0.05": n_sig,
            "n_significant_fdr_lfc_combo": int(len(sig)),
            "top_up_5":              top_up,
            "top_down_5":            top_down,
            "alpha":                 self.alpha,
            "lfc_min_for_summary":   self.lfc_min_for_summary,
        }
        sj_path = out_dir / CONTRACT.output_files["de_summary_json"]
        sj_path.write_text(json.dumps(summary, indent=2,
                                        default=str),
                            encoding="utf-8")
        return {
            "de_table_csv":   str(de_path),
            "de_summary_json": str(sj_path),
            **summary,
        }


def get_solver(method: str = "welch_t", alpha: float = 0.05,
                lfc_min_for_summary: float = 1.0
                ) -> DifferentialExpressionLimmaSolver:
    return DifferentialExpressionLimmaSolver(
        method=method, alpha=alpha,
        lfc_min_for_summary=lfc_min_for_summary)


# ---------------------------------------------------------------------------
# GT selftest
# ---------------------------------------------------------------------------
def _gt_a_welch_matches_scipy() -> List[str]:
    """GT-A — welch_t method on toy data must reproduce scipy.ttest_ind
    (Welch=True) p-values to 1e-9 on a per-gene basis."""
    import tempfile
    rng = np.random.default_rng(2026)
    n_genes, nA, nB = 60, 4, 4
    base = rng.normal(loc=8.0, scale=0.5, size=(nA + nB, n_genes))
    # log-scale data → no log2 needed; but we feed (2^x − 1) so that the
    # operator's internal log2(x+1) recovers the same x.
    raw = (2.0 ** base) - 1.0
    raw = np.clip(raw, 0.0, None)
    samples = [f"S{i+1}" for i in range(nA + nB)]
    df = pd.DataFrame(raw, columns=[f"G{i}" for i in range(n_genes)])
    df.insert(0, "sample_id", samples)
    df.insert(1, "group", ["control"] * nA + ["treated"] * nB)
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(method="welch_t").run(
            df, ColumnMapping({"sample_id_col": "sample_id",
                                 "group_col": "group"}),
            Path(tmp))
        res = pd.read_csv(out["de_table_csv"]).set_index("gene_id")
        for gi in [0, 5, 30, 45, 59]:
            A = base[:nA, gi]
            B = base[nA:, gi]
            tt = sps.ttest_ind(B, A, equal_var=False)
            if abs(res.loc[f"G{gi}", "p_value"] - float(tt.pvalue)) > 1e-9:
                diffs.append(
                    f"[A] G{gi} welch p mismatch with scipy: "
                    f"op={res.loc[f'G{gi}','p_value']:.6e} "
                    f"scipy={float(tt.pvalue):.6e}")
        # n_samples
        if int(out["n_samples_control"]) != nA:
            diffs.append(f"[A] n_samples_control={out['n_samples_control']}")
        if int(out["n_samples_treated"]) != nB:
            diffs.append(f"[A] n_samples_treated={out['n_samples_treated']}")
        if out["group_control"] != "control" or out["group_treated"] != "treated":
            diffs.append(f"[A] group labels wrong: "
                          f"ctrl={out['group_control']} trt={out['group_treated']}")
    return diffs


def _gt_b_injected_degs_top() -> List[str]:
    """GT-B — inject 10 known DEGs (large logFC) and check they all
    surface in the top-10 by adj_p_value with welch_t method."""
    import tempfile
    rng = np.random.default_rng(7)
    n_genes, nA, nB = 200, 5, 5
    base = rng.normal(loc=6.0, scale=0.4, size=(nA + nB, n_genes))
    # inject DEGs in genes 0..9: group B is +3 log2 units
    base[nA:, :10] += 3.0
    raw = np.clip((2.0 ** base) - 1.0, 0.0, None)
    df = pd.DataFrame(raw, columns=[f"G{i}" for i in range(n_genes)])
    df.insert(0, "group", ["control"] * nA + ["treated"] * nB)
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(method="welch_t").run(
            df, ColumnMapping({"group_col": "group"}), Path(tmp))
        res = pd.read_csv(out["de_table_csv"])
        top10 = res.sort_values("adj_p_value").head(10)["gene_id"].tolist()
        injected = {f"G{i}" for i in range(10)}
        recall = len(set(top10) & injected) / 10.0
        if recall < 0.9:
            diffs.append(f"[B] injected DEG top-10 recall too low: {recall}")
        # log2FoldChange of injected should be positive (treated > control)
        for gi in range(10):
            row = res[res["gene_id"] == f"G{gi}"].iloc[0]
            if row["log2FoldChange"] <= 0:
                diffs.append(
                    f"[B] G{gi} log2FC should be +ve (treated up), "
                    f"got {row['log2FoldChange']:.3f}")
                break
    return diffs


def _gt_c_deseq2_integer_counts() -> List[str]:
    """GT-C — deseq2 method on integer count data: injected DEGs should
    appear at the top with positive log2FoldChange.

    Test is qualitative (PyDESeq2 has its own filters and shrinkage so
    we don't compare to a fixed value, but we DO require the injected
    up-regulated genes to be statistically significant in the right
    direction).
    """
    import tempfile
    rng = np.random.default_rng(11)
    n_genes, nA, nB = 100, 6, 6
    # Negative-binomial-ish counts
    mu = rng.uniform(20, 200, size=n_genes)
    cnt_a = rng.poisson(lam=mu[None, :], size=(nA, n_genes))
    cnt_b = rng.poisson(lam=mu[None, :], size=(nB, n_genes))
    # inject 10 up-regulated genes in group B (10x lift)
    cnt_b[:, :10] = rng.poisson(lam=mu[None, :10] * 10.0,
                                  size=(nB, 10))
    counts = np.vstack([cnt_a, cnt_b])
    df = pd.DataFrame(counts, columns=[f"G{i}" for i in range(n_genes)])
    df.insert(0, "group", ["control"] * nA + ["treated"] * nB)
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver(method="deseq2").run(
                df, ColumnMapping({"group_col": "group"}), Path(tmp))
        except Exception as e:
            diffs.append(f"[C] deseq2 failed unexpectedly: "
                          f"{type(e).__name__}: {e}")
            return diffs
        res = pd.read_csv(out["de_table_csv"])
        sig = res[(res["adj_p_value"] < 0.05) &
                   (res["log2FoldChange"] > 1.0)]
        injected = {f"G{i}" for i in range(10)}
        # at least 7 of the 10 injected genes should be significant +ve
        hit = len(set(sig["gene_id"]) & injected)
        if hit < 7:
            diffs.append(
                f"[C] only {hit}/10 injected up-DEGs significant in "
                f"deseq2 output (need ≥7)")
        # method label
        if out["method"] != "deseq2":
            diffs.append(f"[C] method label={out['method']} expected deseq2")
    return diffs


def _gt_d_auto_picks_deseq2_for_counts() -> List[str]:
    """GT-D — method='auto' must pick deseq2 for integer counts and
    welch_t for log-scale floats."""
    import tempfile
    rng = np.random.default_rng(3)
    nA = nB = 4

    # integer counts → expect deseq2
    cnt = rng.poisson(lam=50, size=(nA + nB, 30))
    df_int = pd.DataFrame(cnt, columns=[f"G{i}" for i in range(30)])
    df_int.insert(0, "group", ["A"] * nA + ["B"] * nB)
    # log-scale floats → expect welch_t
    flt = rng.normal(loc=6.0, scale=0.5, size=(nA + nB, 30))
    df_flt = pd.DataFrame(flt, columns=[f"G{i}" for i in range(30)])
    df_flt.insert(0, "group", ["A"] * nA + ["B"] * nB)

    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out_i = get_solver(method="auto").run(
            df_int, ColumnMapping({"group_col": "group"}), Path(tmp + "_i"))
        out_f = get_solver(method="auto").run(
            df_flt, ColumnMapping({"group_col": "group"}), Path(tmp + "_f"))
        if out_i["method"] != "deseq2":
            diffs.append(
                f"[D] auto on integer counts should pick deseq2, "
                f"got {out_i['method']}")
        if out_f["method"] != "welch_t":
            diffs.append(
                f"[D] auto on float log-scale should pick welch_t, "
                f"got {out_f['method']}")
    return diffs


def selftest() -> Dict[str, Any]:
    """4-scenario GT suite for differential_expression_limma.

      GT-A  welch_t == scipy.ttest_ind(equal_var=False) per-gene
      GT-B  injected DEG top-10 recall ≥ 90% (welch_t)
      GT-C  deseq2: injected up-regulated counts recovered (≥7/10)
      GT-D  method='auto' routes integer→deseq2, float→welch_t
    """
    diffs = (_gt_a_welch_matches_scipy()
             + _gt_b_injected_degs_top()
             + _gt_c_deseq2_integer_counts()
             + _gt_d_auto_picks_deseq2_for_counts())
    return {
        "ok": len(diffs) == 0,
        "summary": ("4/4 pass: welch==scipy, DEG top-10 recall, "
                    "deseq2 NB recovery, auto routing"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["differential_expression_limma"],
                    "n_scenarios": 4},
    }


if __name__ == "__main__":
    rep = selftest()
    print(f"differential_expression_limma SELFTEST: "
           f"{'PASS' if rep['ok'] else 'FAIL'}")
    print(f"  {rep['summary']}")
    for d in rep["details"]["diffs"]:
        print(f"  {d}")
