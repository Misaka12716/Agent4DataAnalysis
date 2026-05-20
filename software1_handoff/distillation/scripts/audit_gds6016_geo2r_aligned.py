"""GEO2R-aligned audit on GDS6016.

Why this script exists:
  Our previous bio audit collapsed probes to genes BEFORE limma
  (max-of-probes per gene), then compared the resulting gene-level top
  table to GEO2R's TSV (which is probe-level).  The two sides used
  *different aggregation rules*, which is itself a confounder.

  This script eliminates that confounder by adopting GEO2R's own
  convention end-to-end:

    1. Run our limma_deg_two_group on the PROBE-level expression
       matrix (no expression-level collapse).
    2. Apply ``probe_deg_collapse_to_gene`` (min adj_p_value per gene
       symbol) to our probe-level DEG output.
    3. Apply the SAME min-adj_p collapse to GEO2R's probe-level TSV.
    4. Compare the two gene-level tables: Spearman correlation on
       shared genes, top-K Jaccard, signed-direction agreement, and a
       few sanity oracles.

  Now the only methodological difference between the two sides is the
  limma implementation itself (our pure-Python re-implementation vs
  R/Bioconductor's limma running on GEO).  Any agreement we observe is
  attributable to the differential test, not to upstream choices.

Outputs go under
``benchmark/Software1_Bench/real_medical_data/_geo2r_aligned/<ts>/``.

中文说明
========
**与 GEO2R 公平对比**：双方在 probe 级做检验，再用相同的 **min(adj_p) 每基因
保留最佳 probe** 收敛到基因表，最后比 Spearman / top-K Jaccard。这样能消除
「先聚合表达再 limma」与「GEO2R 探针表」归因不同的问题。
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from distillation.software1_pipeline_demo_app.registry import make_solver
from distillation.software1_solver.contract import ColumnMapping


SOFT_PATH = (ROOT / "benchmark" / "Software1_Bench" / "real_medical_data"
                  / "GDS6016_full.soft")
GEO2R_TSV = (ROOT / "benchmark" / "Software1_Bench" / "real_medical_data"
                  / "references" / "GSE51612.top.table.tsv")
OUT_ROOT = (ROOT / "benchmark" / "Software1_Bench" / "real_medical_data"
                  / "_geo2r_aligned")


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%dT%H%M%S")


def _section(title: str):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _run_solver(solver_id: str, df_in: pd.DataFrame, mapping: dict,
                  out_dir: Path) -> Dict[str, Any]:
    solver = make_solver(solver_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    return solver.run(df=df_in, mapping=ColumnMapping(mapping),
                       output_dir=out_dir)


def _collapse_geo2r(out_dir: Path) -> Path:
    """Apply min-adj_p per Gene.symbol collapse to GEO2R's probe-level TSV.

    Returns path to gene_deg table.  Tie-break: smaller P.Value, larger |logFC|.
    Empty / NaN Gene.symbol probes are dropped (matching ``drop_unmapped=True``
    on our side).
    """
    geo = pd.read_csv(GEO2R_TSV, sep="\t")
    geo["Gene.symbol"] = geo["Gene.symbol"].astype(object)
    geo["Gene.symbol"] = geo["Gene.symbol"].apply(
        lambda v: None if v is None or (isinstance(v, float) and np.isnan(v))
        or (isinstance(v, str) and not v.strip())
        else (v.strip() if isinstance(v, str) else str(v)))
    geo = geo.dropna(subset=["Gene.symbol"]).copy()
    geo["__abs_logfc__"] = geo["logFC"].abs()
    geo = geo.sort_values(
        by=["adj.P.Val", "P.Value", "__abs_logfc__"],
        ascending=[True, True, False], kind="mergesort")
    n_per_gene = (geo.groupby("Gene.symbol", sort=False).size()
                      .rename("n_probes_for_gene"))
    gene = (geo.drop_duplicates(subset=["Gene.symbol"], keep="first")
                 .merge(n_per_gene, left_on="Gene.symbol",
                          right_index=True, how="left"))
    gene = gene.drop(columns=["__abs_logfc__"])
    gene = gene.rename(columns={"Gene.symbol": "gene_symbol",
                                  "ID":          "probe_id",
                                  "adj.P.Val":   "adj_p_value",
                                  "P.Value":     "p_value"})
    gene = gene.sort_values(by=["adj_p_value", "p_value"], kind="mergesort")
    front = ["gene_symbol", "probe_id"]
    gene = gene[front + [c for c in gene.columns if c not in front]]
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "geo2r_gene_deg_table.csv"
    gene.to_csv(p, index=False)
    print(f"[geo2r] {len(geo)} probes (with gene symbol) → {len(gene)} genes")
    return p


def _compare(our_csv: Path, geo_csv: Path, out_dir: Path) -> Dict[str, Any]:
    """Spearman + top-K Jaccard + sign agreement on shared genes."""
    a = pd.read_csv(our_csv).set_index("gene_symbol")
    b = pd.read_csv(geo_csv).set_index("gene_symbol")
    shared = sorted(set(a.index) & set(b.index))
    print(f"[compare] our genes={len(a)}, geo2r genes={len(b)}, "
          f"shared={len(shared)}")
    a_s = a.loc[shared]
    b_s = b.loc[shared]
    rho, p_rho = spearmanr(a_s["logFC"], b_s["logFC"])

    # signed direction agreement (ignoring zero-fold-change rows)
    sign_a = np.sign(a_s["logFC"].fillna(0).to_numpy())
    sign_b = np.sign(b_s["logFC"].fillna(0).to_numpy())
    nz = (sign_a != 0) & (sign_b != 0)
    sign_match = float((sign_a[nz] == sign_b[nz]).mean()) if nz.any() else float("nan")

    # top-K jaccard at multiple K
    a_rank = a.sort_values("adj_p_value", kind="mergesort")
    b_rank = b.sort_values("adj_p_value", kind="mergesort")
    jaccard_at = {}
    overlap_at = {}
    for K in (10, 25, 50, 100, 200, 500):
        ta = set(a_rank.head(K).index)
        tb = set(b_rank.head(K).index)
        inter = ta & tb
        union = ta | tb
        jaccard_at[K] = (len(inter) / len(union)) if union else 0.0
        overlap_at[K] = len(inter)

    # rank-based Spearman of -log10(adj_p_value) (more stable than raw)
    eps = 1e-300
    a_log = -np.log10(np.clip(a_s["adj_p_value"].to_numpy(), eps, 1))
    b_log = -np.log10(np.clip(b_s["adj_p_value"].to_numpy(), eps, 1))
    rho_logp, p_rho_logp = spearmanr(a_log, b_log)

    # Quick visual (head)
    a_head = a_rank.head(10)[["adj_p_value", "logFC"]]
    b_head = b_rank.head(10)[["adj_p_value", "logFC"]]
    print()
    print("Our  top-10 (probe-level limma → gene collapse):")
    print(a_head.to_string())
    print()
    print("GEO2R top-10 (probe-level limma → gene collapse):")
    print(b_head.to_string())

    res = {
        "n_our_genes":         int(len(a)),
        "n_geo2r_genes":       int(len(b)),
        "n_shared_genes":      int(len(shared)),
        "spearman_logFC":      float(rho),
        "spearman_logFC_p":    float(p_rho),
        "spearman_neg_log10_adj_p": float(rho_logp),
        "spearman_neg_log10_adj_p_p": float(p_rho_logp),
        "sign_agreement":      sign_match,
        "n_signed_compared":   int(nz.sum()),
        "topk_jaccard":        jaccard_at,
        "topk_overlap":        overlap_at,
    }
    (out_dir / "comparison.json").write_text(
        json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    return res


def main():
    if not SOFT_PATH.is_file():
        raise SystemExit(f"missing SOFT: {SOFT_PATH}")
    if not GEO2R_TSV.is_file():
        raise SystemExit(f"missing GEO2R TSV: {GEO2R_TSV}")

    run_dir = OUT_ROOT / _ts()
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[run] out = {run_dir}")

    # ---------- Step 1. parse SOFT ---------------------------------
    _section("Step 1 — parse GDS6016.soft")
    out_soft = run_dir / "00_soft_parser"
    soft_res = _run_solver(
        "gds_soft_parser",
        df_in=pd.DataFrame({"_": [str(SOFT_PATH)]}),
        mapping={"soft_path": str(SOFT_PATH)},
        out_dir=out_soft,
    )
    expr_csv = soft_res["expression_matrix_csv"]
    sg_csv   = soft_res["sample_groups_csv"]
    ann_csv  = soft_res["annotation_csv"]
    print(json.dumps({k: v for k, v in soft_res.items()
                       if isinstance(v, (int, float, str))},
                      indent=2, ensure_ascii=False))

    # ---------- Step 2. PROBE-level limma --------------------------
    _section("Step 2 — limma_deg_two_group ON PROBE-LEVEL EXPRESSION MATRIX")
    out_limma = run_dir / "02_limma_probe"
    expr_df = pd.read_csv(expr_csv)
    limma_res = _run_solver(
        "limma_deg_two_group",
        df_in=expr_df,
        mapping={
            "gene_matrix_csv":   expr_csv,            # probe-level here
            "sample_groups_csv": sg_csv,
            "group_a":           "En2 wildtype",
            "group_b":           "En2 knockout",
            "moderation":        True,
            "group_field":       "group_description",
        },
        out_dir=out_limma,
    )
    probe_deg_csv = limma_res["deg_table_csv"]
    print(json.dumps({k: v for k, v in limma_res.items()
                       if isinstance(v, (int, float, str, bool))},
                      indent=2, ensure_ascii=False))

    # ---------- Step 3. probe → gene collapse on OUR DEG -----------
    _section("Step 3 — probe_deg_collapse_to_gene (min adj_p per gene)")
    out_collapse = run_dir / "03_collapse"
    probe_deg_df = pd.read_csv(probe_deg_csv)
    collapse_res = _run_solver(
        "probe_deg_collapse_to_gene",
        df_in=probe_deg_df,
        mapping={
            "deg_table_csv":  probe_deg_csv,
            "annotation_csv": ann_csv,
        },
        out_dir=out_collapse,
    )
    our_gene_csv = collapse_res["gene_deg_table_csv"]
    print(json.dumps({k: v for k, v in collapse_res.items()
                       if isinstance(v, (int, float, str, bool))},
                      indent=2, ensure_ascii=False))

    # ---------- Step 4. SAME collapse on GEO2R ---------------------
    _section("Step 4 — apply identical min-adj_p collapse to GEO2R TSV")
    geo_collapse_dir = run_dir / "04_geo2r_collapse"
    geo_gene_csv = _collapse_geo2r(geo_collapse_dir)

    # ---------- Step 5. compare ------------------------------------
    _section("Step 5 — gene-level comparison (apples to apples)")
    cmp_dir = run_dir / "05_comparison"
    cmp_dir.mkdir(parents=True, exist_ok=True)
    cmp = _compare(Path(our_gene_csv), Path(geo_gene_csv), cmp_dir)

    # ---------- Step 6. before / after summary ---------------------
    _section("Step 6 — verdict")
    print(f"shared genes                 : {cmp['n_shared_genes']}")
    print(f"Spearman ρ on logFC          : {cmp['spearman_logFC']:.4f}  "
          f"(p={cmp['spearman_logFC_p']:.2e})")
    print(f"Spearman ρ on -log10(adj_p)  : {cmp['spearman_neg_log10_adj_p']:.4f}  "
          f"(p={cmp['spearman_neg_log10_adj_p_p']:.2e})")
    print(f"signed direction agreement   : {cmp['sign_agreement']:.4f}  "
          f"(n_compared={cmp['n_signed_compared']})")
    for K in (10, 25, 50, 100, 200, 500):
        print(f"top-{K:>4d} jaccard            : {cmp['topk_jaccard'][K]:.4f}  "
              f"(overlap={cmp['topk_overlap'][K]})")

    # Sanity oracle: well-known ground-truth top probe is Fn3krp
    # (per GEO2R, see references/GSE51612.top.table.tsv).
    our_top = pd.read_csv(our_gene_csv).head(10)["gene_symbol"].tolist()
    expected_top1 = "Fn3krp"
    print()
    print(f"Our  top-10 genes = {our_top}")
    print(f"Sanity: expected top-1 = {expected_top1!r}; "
          f"matched = {our_top[0] == expected_top1}")

    summary = {
        "run_dir":       str(run_dir),
        "soft":          str(SOFT_PATH),
        "geo2r_tsv":     str(GEO2R_TSV),
        "our_gene_csv":  str(our_gene_csv),
        "geo2r_gene_csv":str(geo_gene_csv),
        "comparison":    cmp,
        "our_top10":     our_top,
        "sanity_top1":   bool(our_top[0] == expected_top1),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"[done] full summary at {run_dir/'summary.json'}")


if __name__ == "__main__":
    main()
