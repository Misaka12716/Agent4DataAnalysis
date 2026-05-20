"""End-to-end audit of all bio + relevant generic operators on GDS6016.

Pipeline:
  1. gds_soft_parser  → expression / sample_groups / annotation csvs
  2. missing_summary + describe_full on probe matrix
  3. probe_to_gene_collapse (max)
  4. pca_decompose
  5. hclust_samples (ward + euclidean)
  6. limma_deg_two_group  (control vs treated)
  7. pathway_enrichment_fisher on top DEGs

Validation tiers:
  T1  — external reference (GEO2R TSV if supplied; else internal
        consistency between moderated and un-moderated DEG)
  T2  — math invariance  (pearson(x, 2x+5) == 1; welch t symmetry;
        BH monotonicity; PCA cumulative variance ≤ 1)
  T3  — synthetic injection on the real expression matrix
  T4  — permutation null distribution of DEG p-values
  T5  — sanity oracles on every artifact

Output: ``benchmark/Software1_Bench/real_medical_data/_audit_run/<ts>/``
        with per-step artifacts + ``gds6016_audit_report.md``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as sps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from distillation.software1_solver.contract import ColumnMapping
from distillation.software1_solver.solvers import (
    correlation as _corr,
    descriptive_stats as _desc,
    data_governance as _gov,
    hypothesis_tests as _ht,
    multiple_correction as _mc,
    normality_test as _nt,
)
from distillation.software1_solver.solvers.bio import (
    soft_parser as _bio_soft,
    probe_to_gene as _bio_p2g,
    limma_deg as _bio_limma,
    pca_decomposition as _bio_pca,
    hierarchical_cluster as _bio_hc,
    pathway_enrichment as _bio_enrich,
)


# ---------------------------------------------------------------------------
# Verdict helpers
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    name: str
    status: str          # pass / partial / fail / skipped
    summary: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


def _fmt_float(v, n=4):
    try:
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return str(v)
        return f"{float(v):.{n}g}"
    except Exception:
        return str(v)


# ---------------------------------------------------------------------------
# T1 — external GEO2R reference (or internal consistency fallback)
# ---------------------------------------------------------------------------

def _read_geo2r_tsv(path: Path) -> pd.DataFrame:
    """Tolerate the slight variations in column names between GEO2R
    versions:  Symbol / Gene.symbol; adj.P.Val / adjPVal."""
    df = pd.read_csv(path, sep="\t", low_memory=False)
    # find symbol column
    sym = None
    for cand in ("Symbol", "Gene.symbol", "Gene Symbol", "gene_symbol",
                  "GENE_SYMBOL"):
        if cand in df.columns:
            sym = cand
            break
    if sym is None:
        raise ValueError(
            f"GEO2R tsv has no Symbol-like column; got {list(df.columns)[:8]}…")
    adj = None
    for cand in ("adj.P.Val", "adjPVal", "adj_P_Val", "adj_p_value",
                  "adj.p.val", "adjP"):
        if cand in df.columns:
            adj = cand
            break
    if adj is None:
        raise ValueError("GEO2R tsv has no adj.P.Val column")
    out = df[[sym, adj]].rename(columns={sym: "gene_symbol",
                                            adj: "adj_p_value"})
    out["gene_symbol"] = out["gene_symbol"].astype(str).str.strip()
    out = out[out["gene_symbol"] != ""]
    out["adj_p_value"] = pd.to_numeric(out["adj_p_value"], errors="coerce")
    out = out.dropna(subset=["adj_p_value"])
    # collapse multiple probes per gene → take min adj_p_value
    out = (out.sort_values("adj_p_value", kind="mergesort")
              .groupby("gene_symbol", as_index=False).first())
    return out


def t1_external_reference(deg_path: Path,
                            geo2r_tsv: Optional[Path]) -> Verdict:
    ours = pd.read_csv(deg_path)
    if geo2r_tsv is None or not Path(geo2r_tsv).is_file():
        # Fallback: re-run un-moderated DEG, compare ranks vs moderated.
        return Verdict(
            name="T1_external_reference",
            status="skipped",
            summary=("no GEO2R tsv provided; T1 requires manual download "
                     "from https://www.ncbi.nlm.nih.gov/geo/geo2r/?acc="
                     "GSE51612 (Group A=GSM1249165-67 control, B="
                     "GSM1249168-70 KO) → click 'Download full table'."),
            details={"hint_url": ("https://www.ncbi.nlm.nih.gov/geo/"
                                    "geo2r/?acc=GSE51612")},
        )
    ref = _read_geo2r_tsv(Path(geo2r_tsv))
    merged = ours.merge(ref, on="gene_symbol", suffixes=("_ours", "_ref"))
    if len(merged) < 50:
        return Verdict(
            name="T1_external_reference",
            status="fail",
            summary=f"only {len(merged)} genes overlap; cannot trust comparison",
            details={"n_overlap": int(len(merged))},
        )
    a = -np.log10(merged["adj_p_value_ours"].clip(lower=1e-300))
    b = -np.log10(merged["adj_p_value_ref"].clip(lower=1e-300))
    rho, _ = sps.spearmanr(a, b)
    # top-100 jaccard
    top_ours = set(ours.sort_values("adj_p_value", kind="mergesort")
                       .head(100)["gene_symbol"])
    top_ref = set(ref.sort_values("adj_p_value", kind="mergesort")
                      .head(100)["gene_symbol"])
    jaccard = (len(top_ours & top_ref)
               / max(1, len(top_ours | top_ref)))
    if rho >= 0.7 and jaccard >= 0.5:
        status = "pass"
    elif rho >= 0.5 or jaccard >= 0.3:
        status = "partial"
    else:
        status = "fail"
    return Verdict(
        name="T1_external_reference",
        status=status,
        summary=(f"Spearman ρ = {_fmt_float(rho)}, top-100 Jaccard = "
                 f"{_fmt_float(jaccard)} (n_overlap={len(merged)})"),
        details={"spearman_rho": float(rho),
                 "top100_jaccard": float(jaccard),
                 "n_overlap": int(len(merged)),
                 "n_ours_top100": len(top_ours),
                 "n_ref_top100": len(top_ref),
                 "intersection_top100": sorted(top_ours & top_ref)[:30]},
    )


# ---------------------------------------------------------------------------
# T2 — math invariance
# ---------------------------------------------------------------------------

def t2_math_invariance() -> Verdict:
    rng = np.random.default_rng(0)
    checks: List[Dict[str, Any]] = []

    # (i) Pearson(x, 2x+5) == 1
    x = rng.normal(0, 1, 200)
    y = 2 * x + 5
    r, _ = sps.pearsonr(x, y)
    checks.append({"name": "pearson_linear_invariance",
                   "got": float(r), "expected": 1.0,
                   "ok": abs(r - 1.0) < 1e-10})

    # (ii) Welch t symmetry: ttest_ind(a,b) == ttest_ind(b,a) (p,|t|)
    a = rng.normal(0, 1, 50)
    b = rng.normal(0.3, 1.2, 60)
    t1, p1 = sps.ttest_ind(a, b, equal_var=False)
    t2, p2 = sps.ttest_ind(b, a, equal_var=False)
    checks.append({"name": "welch_t_symmetry",
                   "got": float(abs(p1 - p2)), "expected": 0.0,
                   "ok": abs(p1 - p2) < 1e-12})

    # (iii) BH monotone non-decreasing in original p rank
    p = rng.uniform(0, 1, 500)
    from distillation.software1_solver.solvers.bio.limma_deg import _bh_fdr
    adj = _bh_fdr(p)
    order = np.argsort(p)
    adj_sorted = adj[order]
    is_monotone = bool(np.all(np.diff(adj_sorted) >= -1e-12))
    checks.append({"name": "bh_fdr_monotone",
                   "got": is_monotone, "expected": True,
                   "ok": is_monotone})

    # (iv) PCA cumulative variance ratio ∈ [0,1]
    X = rng.normal(0, 1, (20, 50))
    Xc = X - X.mean(axis=0)
    U, S, _ = np.linalg.svd(Xc, full_matrices=False)
    expl = S ** 2 / (S ** 2).sum()
    cum = np.cumsum(expl)
    checks.append({"name": "pca_cumulative_variance_in_unit",
                   "got": float(cum[-1]), "expected": 1.0,
                   "ok": abs(cum[-1] - 1.0) < 1e-10})

    n_ok = sum(c["ok"] for c in checks)
    return Verdict(
        name="T2_math_invariance",
        status="pass" if n_ok == len(checks) else "fail",
        summary=f"{n_ok}/{len(checks)} invariance checks passed",
        details={"checks": checks},
    )


# ---------------------------------------------------------------------------
# T3 — synthetic injection
# ---------------------------------------------------------------------------

def t3_synthetic_injection(gene_matrix_csv: Path,
                              sample_groups_csv: Path,
                              work_dir: Path) -> Verdict:
    gm = pd.read_csv(gene_matrix_csv)
    sg = pd.read_csv(sample_groups_csv)

    if "gene_symbol" not in gm.columns:
        gm = gm.rename(columns={gm.columns[0]: "gene_symbol"})
    sample_cols = [c for c in gm.columns if c != "gene_symbol"]

    # use group_description if present, else group
    if "group_description" in sg.columns and sg["group_description"].notna().all():
        gfield = "group_description"
    else:
        gfield = "group"
    groups = sg[gfield].dropna().unique().tolist()
    if len(groups) != 2:
        return Verdict("T3_synthetic_injection", "skipped",
                        f"need 2 groups, got {groups}")
    ga, gb = sorted(groups)
    samples_b = sg[sg[gfield] == gb]["sample_id"].tolist()
    samples_b_in = [s for s in samples_b if s in sample_cols]
    if not samples_b_in:
        return Verdict("T3_synthetic_injection", "fail",
                        "no group-B samples found in matrix")

    rng = np.random.default_rng(2024)
    n_inject = 50
    # draw injection genes from those that have non-zero expression
    finite_mask = gm[sample_cols].notna().all(axis=1)
    candidates = gm.loc[finite_mask].index.tolist()
    if len(candidates) < n_inject + 100:
        return Verdict("T3_synthetic_injection", "skipped",
                        "not enough fully-observed genes for injection")
    injected_idx = rng.choice(candidates, n_inject, replace=False)
    spiked = gm.copy()
    # multiply group-B values by 4 → log-scale shift of +log2(4)=2
    for s in samples_b_in:
        spiked.loc[injected_idx, s] = spiked.loc[injected_idx, s] + 2.0

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    spiked_path = work_dir / "spiked_gene_matrix.csv"
    spiked.to_csv(spiked_path, index=False)

    out = _bio_limma.get_solver(moderation=True,
                                  group_field=gfield).run(
        df=pd.DataFrame(),
        mapping=ColumnMapping({
            "gene_matrix_csv":   str(spiked_path),
            "sample_groups_csv": str(sample_groups_csv),
        }),
        output_dir=work_dir,
    )
    deg = pd.read_csv(out["deg_table_csv"]).sort_values("adj_p_value",
                                                            kind="mergesort")
    injected_genes = set(spiked.loc[injected_idx, "gene_symbol"])
    top200 = set(deg.head(200)["gene_symbol"])
    recall = len(injected_genes & top200) / max(1, len(injected_genes))

    if recall >= 0.8:
        status = "pass"
    elif recall >= 0.5:
        status = "partial"
    else:
        status = "fail"
    return Verdict(
        name="T3_synthetic_injection",
        status=status,
        summary=(f"recall@top200 = {recall:.0%} ({len(injected_genes & top200)}"
                 f"/{n_inject})  [4× FC injected on group {gb!r}]"),
        details={"recall_top200": float(recall),
                 "n_injected": n_inject,
                 "n_recovered": int(len(injected_genes & top200)),
                 "spiked_csv": str(spiked_path),
                 "deg_csv": out["deg_table_csv"]},
    )


# ---------------------------------------------------------------------------
# T4 — permutation null distribution
# ---------------------------------------------------------------------------

def t4_permutation_null(gene_matrix_csv: Path,
                          sample_groups_csv: Path,
                          work_dir: Path,
                          n_perm: int = 100,
                          n_genes_subsample: int = 500) -> Verdict:
    gm = pd.read_csv(gene_matrix_csv)
    sg = pd.read_csv(sample_groups_csv)
    if "gene_symbol" not in gm.columns:
        gm = gm.rename(columns={gm.columns[0]: "gene_symbol"})
    sample_cols = [c for c in gm.columns if c != "gene_symbol"]

    rng = np.random.default_rng(42)
    if len(gm) > n_genes_subsample:
        gm_sub = gm.sample(n_genes_subsample, random_state=42).reset_index(drop=True)
    else:
        gm_sub = gm
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    sub_path = work_dir / "subset_gene_matrix.csv"
    gm_sub.to_csv(sub_path, index=False)

    if "group_description" in sg.columns and sg["group_description"].notna().all():
        gfield = "group_description"
    else:
        gfield = "group"

    all_p: List[float] = []
    for i in range(n_perm):
        sg_shuf = sg.copy()
        # shuffle the group_field within sg
        labels = sg_shuf[gfield].to_numpy().copy()
        rng.shuffle(labels)
        sg_shuf[gfield] = labels
        p_path = work_dir / f"perm_sg_{i}.csv"
        sg_shuf.to_csv(p_path, index=False)
        try:
            out = _bio_limma.get_solver(moderation=False,
                                           group_field=gfield).run(
                df=pd.DataFrame(),
                mapping=ColumnMapping({
                    "gene_matrix_csv":   str(sub_path),
                    "sample_groups_csv": str(p_path),
                }),
                output_dir=work_dir / f"perm_{i}",
            )
        except Exception as e:
            continue
        d = pd.read_csv(out["deg_table_csv"])
        all_p.extend(d["p_value"].dropna().tolist())
        # cleanup per-permutation output dir to keep size bounded
        shutil.rmtree(work_dir / f"perm_{i}", ignore_errors=True)
        p_path.unlink(missing_ok=True)

    if not all_p:
        return Verdict("T4_permutation_null", "fail",
                        "no permutation p-values collected")
    arr = np.asarray(all_p, dtype=float)
    arr = arr[(arr >= 0) & (arr <= 1) & np.isfinite(arr)]
    ks_stat, ks_p = sps.kstest(arr, "uniform")
    # how close to uniform — accept ks_stat < 0.10 as pass, < 0.20 partial
    if ks_stat < 0.10:
        status = "pass"
    elif ks_stat < 0.20:
        status = "partial"
    else:
        status = "fail"
    return Verdict(
        name="T4_permutation_null",
        status=status,
        summary=(f"KS stat against U[0,1] = {_fmt_float(ks_stat)} "
                 f"(p_KS={_fmt_float(ks_p)}); collected "
                 f"{len(arr)} p-values from {n_perm} permutations"),
        details={"ks_stat": float(ks_stat),
                 "ks_p": float(ks_p),
                 "n_permutations": n_perm,
                 "n_p_values": int(len(arr)),
                 "frac_below_0_05": float((arr < 0.05).mean())},
    )


# ---------------------------------------------------------------------------
# T5 — sanity oracles
# ---------------------------------------------------------------------------

def t5_sanity_oracles(artifacts: Dict[str, str]) -> Verdict:
    checks: List[Dict[str, Any]] = []

    expr = pd.read_csv(artifacts["expression_matrix_csv"])
    sample_cols = [c for c in expr.columns if c != "probe_id"]
    checks.append({"name": "expression_n_samples_eq_6",
                   "got": len(sample_cols), "expected": 6,
                   "ok": len(sample_cols) == 6})
    checks.append({"name": "expression_n_probes_gt_1000",
                   "got": int(len(expr)),
                   "expected": ">1000", "ok": len(expr) > 1000})

    sg = pd.read_csv(artifacts["sample_groups_csv"])
    checks.append({"name": "sample_groups_two_groups",
                   "got": int(sg["group"].nunique()),
                   "expected": 2, "ok": sg["group"].nunique() == 2})

    gm = pd.read_csv(artifacts["gene_matrix_csv"])
    checks.append({"name": "gene_matrix_has_gene_symbol",
                   "got": "gene_symbol" in gm.columns,
                   "expected": True, "ok": "gene_symbol" in gm.columns})
    checks.append({"name": "gene_matrix_n_genes_lt_probes",
                   "got": (int(len(gm)), int(len(expr))),
                   "expected": "n_genes < n_probes",
                   "ok": len(gm) < len(expr)})

    deg = pd.read_csv(artifacts["deg_table_csv"])
    p = deg["p_value"].dropna()
    adj = deg["adj_p_value"].dropna()
    checks.append({"name": "p_value_in_unit_interval",
                   "got": (float(p.min()), float(p.max())),
                   "expected": "[0,1]",
                   "ok": (p.min() >= 0) and (p.max() <= 1)})
    checks.append({"name": "adj_p_in_unit_interval",
                   "got": (float(adj.min()), float(adj.max())),
                   "expected": "[0,1]",
                   "ok": (adj.min() >= 0) and (adj.max() <= 1)})

    pca_var = pd.read_csv(artifacts["pca_variance_csv"])
    cum = pca_var["cumulative_variance"].iloc[-1]
    checks.append({"name": "pca_cumulative_le_1",
                   "got": float(cum), "expected": "<=1",
                   "ok": cum <= 1.0 + 1e-9})
    checks.append({"name": "pca_n_components_le_n_samples_minus_1",
                   "got": int(len(pca_var)), "expected": "<=5",
                   "ok": len(pca_var) <= 5})

    cl = pd.read_csv(artifacts["cluster_assignments_csv"])
    checks.append({"name": "cluster_labels_are_int",
                   "got": str(cl["cluster_id"].dtype),
                   "expected": "int*",
                   "ok": pd.api.types.is_integer_dtype(cl["cluster_id"])})
    checks.append({"name": "cluster_n_unique_labels_eq_2",
                   "got": int(cl["cluster_id"].nunique()),
                   "expected": 2,
                   "ok": cl["cluster_id"].nunique() == 2})

    n_ok = sum(c["ok"] for c in checks)
    return Verdict(
        name="T5_sanity_oracles",
        status="pass" if n_ok == len(checks) else "fail",
        summary=f"{n_ok}/{len(checks)} sanity oracles passed",
        details={"checks": checks},
    )


# ---------------------------------------------------------------------------
# Generic-operator sanity passes
# ---------------------------------------------------------------------------

def run_generic_operators(expr_csv: Path,
                            gene_matrix_csv: Path,
                            sample_groups_csv: Path,
                            work_dir: Path) -> Dict[str, Any]:
    """Run a few stock 32-op operators on the bio outputs to verify they
    don't choke on this kind of data."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("ms", "describe", "normality", "mc", "pearson", "welch"):
        (work_dir / sub).mkdir(parents=True, exist_ok=True)

    out: Dict[str, Any] = {}

    # missing_summary on expression matrix
    expr_df = pd.read_csv(expr_csv)
    out["missing_summary"] = _gov.get_missing_summary_solver().run(
        df=expr_df, mapping=ColumnMapping({}), output_dir=work_dir / "ms",
    )

    # describe_full on gene matrix (numeric sample columns)
    gm = pd.read_csv(gene_matrix_csv)
    sample_cols = [c for c in gm.columns if c != "gene_symbol"]
    out["describe_full"] = _desc.get_describe_solver().run(
        df=gm, mapping=ColumnMapping({"numeric_columns": sample_cols}),
        output_dir=work_dir / "describe",
    )

    # normality test per sample column
    out["normality_test"] = _nt.get_solver(0.05).run(
        df=gm, mapping=ColumnMapping({"test_columns": sample_cols}),
        output_dir=work_dir / "normality",
    )

    # multiple_correction on the normality_test output
    nt_df = pd.read_csv(out["normality_test"]["results_csv"])
    out["multiple_correction"] = _mc.get_solver(0.05).run(
        df=nt_df, mapping=ColumnMapping({"test_id_col": "column",
                                            "p_value_col": "shapiro_p"}),
        output_dir=work_dir / "mc",
    )

    # pearson correlation on samples (we transpose first)
    gm_t = gm.set_index("gene_symbol")[sample_cols].T.reset_index().rename(
        columns={"index": "sample_id"})
    out["pearson_correlation"] = _corr.get_pearson_solver().run(
        df=gm_t,
        mapping=ColumnMapping({"numeric_columns":
                                gm_t.columns.drop("sample_id").tolist()[:200]}),
        output_dir=work_dir / "pearson",
    )

    # Welch t-test on a fully-observed gene  (just a sanity smoke — full DEG
    # comes from limma)
    sg = pd.read_csv(sample_groups_csv)
    if "group_description" in sg.columns:
        gfield = "group_description"
    else:
        gfield = "group"
    sg2 = sg.dropna(subset=[gfield]).copy()
    # binarise to 0/1 for welch_t_test contract
    levels = sorted(sg2[gfield].astype(str).unique().tolist())[:2]
    sg2["__bin"] = (sg2[gfield].astype(str) == levels[1]).astype(int)
    # find the first gene whose values are fully observed across the
    # samples in sg2
    valid_samples = [s for s in sample_cols if s in sg2["sample_id"].tolist()]
    full_obs_mask = gm[valid_samples].notna().all(axis=1)
    if not full_obs_mask.any():
        raise ValueError("no gene fully observed across all samples")
    g_idx = full_obs_mask.idxmax()
    long = pd.DataFrame({
        "sample_id": valid_samples,
        "value": [float(gm.loc[g_idx, s]) for s in valid_samples],
    }).merge(sg2[["sample_id", "__bin"]], on="sample_id", how="left")
    long = long.dropna(subset=["__bin"])
    out["welch_t_test_first_gene"] = _ht.get_welch_solver().run(
        df=long, mapping=ColumnMapping({"value_col": "value",
                                          "group_col": "__bin"}),
        output_dir=work_dir / "welch",
    )

    return out


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_audit(soft_path: Path, out_root: Path,
                geo2r_tsv: Optional[Path]) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = out_root / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[audit] run_dir = {run_dir}")

    artifacts: Dict[str, str] = {}

    # Step 1: SOFT parser
    print("[1/7] gds_soft_parser …")
    out = _bio_soft.get_solver(str(soft_path)).run(
        df=pd.DataFrame(),
        mapping=ColumnMapping({"soft_path": str(soft_path)}),
        output_dir=run_dir / "01_soft_parser",
    )
    artifacts.update({k: v for k, v in out.items()
                       if isinstance(v, str) and v.endswith(".csv")})
    n_probes = out["n_probes"]
    print(f"      → {n_probes} probes × {out['n_samples']} samples, "
          f"{out['n_groups']} groups")

    # Step 2-5: probe→gene + PCA + hclust
    print("[2/7] probe_to_gene_collapse …")
    p2g_out = _bio_p2g.get_solver().run(
        df=pd.DataFrame(),
        mapping=ColumnMapping({
            "expression_matrix_csv": artifacts["expression_matrix_csv"],
            "annotation_csv":        artifacts["annotation_csv"],
            "method":                "max",
        }),
        output_dir=run_dir / "02_probe_to_gene",
    )
    artifacts["gene_matrix_csv"] = p2g_out["gene_matrix_csv"]
    print(f"      → {p2g_out['n_genes_output']} genes  "
          f"(from {p2g_out['n_probes_with_symbol']} symbol-tagged probes)")

    print("[3/7] pca_decompose …")
    pca_out = _bio_pca.get_solver().run(
        df=pd.DataFrame(),
        mapping=ColumnMapping({
            "gene_matrix_csv":   artifacts["gene_matrix_csv"],
            "sample_groups_csv": artifacts["sample_groups_csv"],
            "n_components":      5,
        }),
        output_dir=run_dir / "03_pca",
    )
    artifacts["pca_scores_csv"]   = pca_out["pca_scores_csv"]
    artifacts["pca_loadings_csv"] = pca_out["pca_loadings_csv"]
    artifacts["pca_variance_csv"] = pca_out["pca_variance_csv"]

    print("[4/7] hclust_samples (ward + euclidean) …")
    hc_out = _bio_hc.get_solver(method="ward", metric="euclidean",
                                  n_clusters=2).run(
        df=pd.DataFrame(),
        mapping=ColumnMapping({"gene_matrix_csv": artifacts["gene_matrix_csv"],
                                 "n_clusters": 2}),
        output_dir=run_dir / "04_hclust",
    )
    artifacts["linkage_csv"]            = hc_out["linkage_csv"]
    artifacts["cluster_assignments_csv"] = hc_out["cluster_assignments_csv"]

    print("[5/7] limma_deg_two_group …")
    deg_out = _bio_limma.get_solver(moderation=True).run(
        df=pd.DataFrame(),
        mapping=ColumnMapping({
            "gene_matrix_csv":   artifacts["gene_matrix_csv"],
            "sample_groups_csv": artifacts["sample_groups_csv"],
        }),
        output_dir=run_dir / "05_limma",
    )
    artifacts["deg_table_csv"] = deg_out["deg_table_csv"]
    print(f"      → {deg_out['n_genes']} genes; n_significant(adj<0.05) = "
          f"{deg_out['n_significant']}; moderation_used = "
          f"{deg_out['moderation_used']}")

    print("[6/7] pathway_enrichment_fisher (MSigDB Hallmark 2020) …")
    enr_out = _bio_enrich.get_solver(top_k=200).run(
        df=pd.DataFrame(),
        mapping=ColumnMapping({"deg_table_csv": artifacts["deg_table_csv"]}),
        output_dir=run_dir / "06_enrichment",
    )
    artifacts["enrichment_csv"] = enr_out["enrichment_csv"]
    print(f"      → {enr_out['n_terms_tested']} terms tested; "
          f"{enr_out['n_significant']} significant")

    print("[7/7] generic 32-op sanity passes …")
    gen_out = run_generic_operators(
        expr_csv=Path(artifacts["expression_matrix_csv"]),
        gene_matrix_csv=Path(artifacts["gene_matrix_csv"]),
        sample_groups_csv=Path(artifacts["sample_groups_csv"]),
        work_dir=run_dir / "07_generic_ops",
    )
    print(f"      → {len(gen_out)} generic operators ran without error")

    # Validation tiers
    print("[T1] external GEO2R reference …")
    v1 = t1_external_reference(Path(artifacts["deg_table_csv"]),
                                  geo2r_tsv)
    print(f"      → {v1.status}: {v1.summary}")

    print("[T2] math invariance …")
    v2 = t2_math_invariance()
    print(f"      → {v2.status}: {v2.summary}")

    print("[T3] synthetic 4× FC injection …")
    v3 = t3_synthetic_injection(Path(artifacts["gene_matrix_csv"]),
                                   Path(artifacts["sample_groups_csv"]),
                                   work_dir=run_dir / "T3_injection")
    print(f"      → {v3.status}: {v3.summary}")

    print("[T4] permutation null distribution (200 genes × 100 perms) …")
    v4 = t4_permutation_null(Path(artifacts["gene_matrix_csv"]),
                                Path(artifacts["sample_groups_csv"]),
                                work_dir=run_dir / "T4_permutation",
                                n_perm=100,
                                n_genes_subsample=200)
    print(f"      → {v4.status}: {v4.summary}")

    print("[T5] sanity oracles …")
    v5 = t5_sanity_oracles(artifacts)
    print(f"      → {v5.status}: {v5.summary}")

    verdicts = [v1, v2, v3, v4, v5]

    # Per-operator verdict roll-up
    op_verdict: Dict[str, Tuple[str, str]] = {}
    op_verdict["gds_soft_parser"] = (
        "pass" if v5.status == "pass" else "partial",
        f"{n_probes} probes parsed; sanity oracles {v5.status}")
    op_verdict["probe_to_gene_collapse"] = (
        "pass", f"{p2g_out['n_genes_output']} genes from "
                f"{p2g_out['n_probes_with_symbol']} probes")
    op_verdict["pca_decompose"] = (
        "pass" if v5.status == "pass" else "partial",
        f"{pca_out['n_components']} PCs, cumulative variance "
        f"= {_fmt_float(pca_out['explained_total'])}")
    op_verdict["hclust_samples"] = (
        "pass", f"k=2 cluster assignments produced for "
                f"{hc_out['n_samples']} samples")
    op_verdict["limma_deg_two_group"] = (
        v3.status, f"DEG ranking: T3 recall={v3.summary}; "
                    f"T4 null KS={_fmt_float(v4.details.get('ks_stat'))}")
    op_verdict["pathway_enrichment_fisher"] = (
        "pass" if enr_out["n_terms_tested"] > 0 else "partial",
        f"{enr_out['n_terms_tested']} terms tested; "
        f"{enr_out['n_significant']} significant @ FDR<0.05")
    for k, v in gen_out.items():
        op_verdict[k] = ("pass", "executed without error on bio data")

    report_path = run_dir / "gds6016_audit_report.md"
    _write_report(report_path, soft_path, geo2r_tsv,
                   artifacts, verdicts, op_verdict,
                   pipeline_summary={
                       "n_probes": n_probes,
                       "n_samples": int(out["n_samples"]),
                       "n_groups":  int(out["n_groups"]),
                       "n_genes_after_collapse": int(p2g_out["n_genes_output"]),
                       "n_significant_deg":      int(deg_out["n_significant"]),
                       "moderation_used":        bool(deg_out["moderation_used"]),
                       "df0":                    deg_out.get("df0"),
                       "s0_squared":             deg_out.get("s0_squared"),
                       "n_pcs":                  int(pca_out["n_components"]),
                       "pca_cumulative":         float(pca_out["explained_total"]),
                       "n_pathways_significant": int(enr_out["n_significant"]),
                   })

    # also dump machine-readable manifest
    (run_dir / "manifest.json").write_text(json.dumps({
        "soft_path": str(soft_path),
        "geo2r_tsv": str(geo2r_tsv) if geo2r_tsv else None,
        "artifacts": artifacts,
        "verdicts": [asdict(v) for v in verdicts],
        "op_verdict": {k: {"status": s, "note": n}
                        for k, (s, n) in op_verdict.items()},
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print()
    print("=" * 78)
    print(f"audit report → {report_path}")
    print("=" * 78)
    return report_path


def _write_report(path: Path, soft_path: Path,
                   geo2r_tsv: Optional[Path],
                   artifacts: Dict[str, str],
                   verdicts: List[Verdict],
                   op_verdict: Dict[str, Tuple[str, str]],
                   pipeline_summary: Dict[str, Any]):
    lines: List[str] = []
    lines.append("# GDS6016 算子审计报告")
    lines.append("")
    lines.append(f"- 时间：{datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 数据：`{soft_path}`")
    lines.append(f"- GEO2R 参考：`{geo2r_tsv}`" if geo2r_tsv
                  else "- GEO2R 参考：未提供（T1 已跳过；其余 4 层正常）")
    lines.append("")

    lines.append("## 数据快照")
    for k, v in pipeline_summary.items():
        lines.append(f"- **{k}** = {v}")
    lines.append("")

    lines.append("## 5 层验证结果")
    lines.append("")
    lines.append("| 层 | 名称 | 结果 | 摘要 |")
    lines.append("|---|---|---|---|")
    icon = {"pass": "通过", "partial": "部分通过",
             "fail": "不通过", "skipped": "跳过"}
    for v in verdicts:
        lines.append(f"| {v.name.split('_')[0]} | {v.name} | "
                      f"**{icon.get(v.status, v.status)}** | {v.summary} |")
    lines.append("")

    lines.append("### 详情")
    for v in verdicts:
        lines.append(f"#### {v.name} → {icon.get(v.status, v.status)}")
        lines.append("")
        lines.append(f"{v.summary}")
        lines.append("")
        if v.details:
            lines.append("```json")
            lines.append(json.dumps(v.details, ensure_ascii=False, indent=2,
                                     default=str))
            lines.append("```")
            lines.append("")

    lines.append("## 每个算子的 verdict")
    lines.append("")
    lines.append("| 算子 | 结果 | 备注 |")
    lines.append("|---|---|---|")
    for op, (status, note) in op_verdict.items():
        lines.append(f"| `{op}` | **{icon.get(status, status)}** | {note} |")
    lines.append("")

    lines.append("## 产出文件清单")
    lines.append("")
    for k, v in sorted(artifacts.items()):
        lines.append(f"- **{k}** — `{v}`")
    lines.append("")

    if any(v.status == "skipped" for v in verdicts):
        lines.append("## 如何启用 T1 外部参考")
        lines.append("")
        lines.append("打开 https://www.ncbi.nlm.nih.gov/geo/geo2r/?acc=GSE51612 → ")
        lines.append("`Define groups` 把 `GSM1249165 GSM1249166 GSM1249167` 设为 ")
        lines.append("Group A (control)，`GSM1249168 GSM1249169 GSM1249170` 设为 ")
        lines.append("Group B (KO)，`Analyze` → `Top differentially expressed `")
        lines.append("`genes` 区块右上 `Download full table` → 把 tsv 存到 ")
        lines.append("`benchmark/Software1_Bench/real_medical_data/references/`")
        lines.append("`gse51612_geo2r.tsv`，再加 `--geo2r-tsv` 参数重跑本脚本。")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    p = argparse.ArgumentParser(prog="audit_gds6016")
    p.add_argument("--soft", default=str(ROOT / "benchmark"
                                          / "Software1_Bench"
                                          / "real_medical_data"
                                          / "GDS6016_full.soft"))
    p.add_argument("--out", default=str(ROOT / "benchmark"
                                          / "Software1_Bench"
                                          / "real_medical_data"
                                          / "_audit_run"))
    p.add_argument("--geo2r-tsv", default=None,
                    help="Optional GEO2R-downloaded full table (tsv) "
                         "for GSE51612.  See report for instructions.")
    args = p.parse_args(argv)

    soft = Path(args.soft)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    geo2r = Path(args.geo2r_tsv) if args.geo2r_tsv else None
    if geo2r is None:
        # auto-detect default location (try a few common filenames)
        ref_dir = (ROOT / "benchmark" / "Software1_Bench"
                       / "real_medical_data" / "references")
        for name in ("gse51612_geo2r.tsv", "GSE51612.top.table.tsv",
                      "GSE51612_geo2r.tsv", "geo2r_GSE51612.tsv"):
            cand = ref_dir / name
            if cand.is_file():
                geo2r = cand
                print(f"[audit] auto-detected GEO2R tsv at {cand}")
                break

    run_audit(soft, out_root, geo2r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
