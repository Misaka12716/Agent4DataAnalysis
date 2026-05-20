"""LLM agent demo (qwen3-8b) — GEO2R-aligned bio pipeline on GDS6016.

Same idea as ``bio_agent_demo.py`` but the natural-language task asks
the LLM to follow GEO2R's convention: differential test on the
PROBE-level expression matrix, then collapse the probe-level DEG table
to gene level using the new ``probe_deg_collapse_to_gene`` solver.
The pathway enrichment runs on the collapsed gene-level table.

After execution we compare the agent's gene-level DEG table to GEO2R's
TSV — both sides go through the same min-adj_p collapse, so the only
remaining methodological difference is the limma implementation
itself.  Expect Spearman ρ on -log10(adj_p) ≈ 1.000 and top-K Jaccard
== 1.000 (matching the hardcoded ``audit_gds6016_geo2r_aligned`` run).

中文说明
========
调用 ``solve_task``：规划 LLM 产出管线，再用 ``step_overrides`` 把路径、
``group_a``/``group_b`` 等关键 mapping **钉死**（防小模型漏 mapping）。
跑完与 GEO2R 双端 min(adj_p) 基因表比对，见输出目录下 ``summary.json``。
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from distillation.software1_pipeline_demo_app import llm_client
from distillation.software1_pipeline_demo_app.registry import make_solver
from distillation.software1_solver.contract import ColumnMapping
from distillation.software1_agent import solve_task


SOFT_PATH = (ROOT / "benchmark" / "Software1_Bench" / "real_medical_data"
                  / "GDS6016_full.soft")
GEO2R_TSV = (ROOT / "benchmark" / "Software1_Bench" / "real_medical_data"
                  / "references" / "GSE51612.top.table.tsv")
OUT_ROOT = (ROOT / "benchmark" / "Software1_Bench" / "real_medical_data"
                  / "_agent_runs_geo2r")


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%dT%H%M%S")


def _collapse_geo2r_tsv() -> pd.DataFrame:
    """Same collapse as audit_gds6016_geo2r_aligned._collapse_geo2r."""
    geo = pd.read_csv(GEO2R_TSV, sep="\t")
    geo["Gene.symbol"] = geo["Gene.symbol"].astype(object).apply(
        lambda v: None if v is None or (isinstance(v, float) and np.isnan(v))
        or (isinstance(v, str) and not v.strip())
        else (v.strip() if isinstance(v, str) else str(v)))
    geo = geo.dropna(subset=["Gene.symbol"]).copy()
    geo["__abs_logfc__"] = geo["logFC"].abs()
    geo = geo.sort_values(by=["adj.P.Val", "P.Value", "__abs_logfc__"],
                            ascending=[True, True, False], kind="mergesort")
    gene = (geo.drop_duplicates(subset=["Gene.symbol"], keep="first")
                 .rename(columns={"Gene.symbol": "gene_symbol",
                                    "adj.P.Val":   "adj_p_value",
                                    "P.Value":     "p_value",
                                    "ID":          "probe_id"}))
    return gene[["gene_symbol", "probe_id", "adj_p_value", "p_value", "logFC"]]


def _compare(agent_gene_csv: Path, geo_df: pd.DataFrame):
    a = pd.read_csv(agent_gene_csv).set_index("gene_symbol")
    b = geo_df.set_index("gene_symbol")
    shared = sorted(set(a.index) & set(b.index))
    a_s = a.loc[shared]; b_s = b.loc[shared]
    rho_lp, _ = spearmanr(
        -np.log10(np.clip(a_s["adj_p_value"], 1e-300, 1)),
        -np.log10(np.clip(b_s["adj_p_value"], 1e-300, 1)),
    )
    rho_fc, _ = spearmanr(a_s["logFC"], b_s["logFC"])
    a_top = a.sort_values("adj_p_value", kind="mergesort")
    b_top = b.sort_values("adj_p_value", kind="mergesort")
    jacc = {}
    for K in (10, 25, 50, 100, 200, 500):
        ta = set(a_top.head(K).index); tb = set(b_top.head(K).index)
        jacc[K] = len(ta & tb) / max(1, len(ta | tb))
    return {
        "n_shared": len(shared),
        "spearman_neg_log10_adj_p": float(rho_lp),
        "abs_spearman_logFC":       float(abs(rho_fc)),
        "topk_jaccard":             jacc,
        "agent_top10":              a_top.head(10).index.tolist(),
        "geo2r_top10":              b_top.head(10).index.tolist(),
    }


def main():
    if not SOFT_PATH.is_file():
        raise SystemExit(f"missing SOFT: {SOFT_PATH}")
    if not GEO2R_TSV.is_file():
        raise SystemExit(f"missing GEO2R TSV: {GEO2R_TSV}")
    cfg = llm_client.get_config()
    if cfg is None:
        raise SystemExit("LLM not configured (.env)")
    print(f"[demo] LLM model={cfg.model}  base={cfg.base_url}")

    run_dir = OUT_ROOT / _ts()
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[demo] run_dir = {run_dir}")

    # Pre-parse SOFT once so the agent does not need to (the parser is
    # stable / has no LLM in the loop).  Feed the agent the
    # sample_groups.csv as the entry-point input — the planner is told
    # the probe matrix path as a literal in the NL task.
    print("[demo] step 0: pre-parsing SOFT …")
    soft_dir = run_dir / "00_soft_parser"
    soft_solver = make_solver("gds_soft_parser")
    soft_dir.mkdir(parents=True, exist_ok=True)
    soft_res = soft_solver.run(
        df=pd.DataFrame({"_": [str(SOFT_PATH)]}),
        mapping=ColumnMapping({"soft_path": str(SOFT_PATH)}),
        output_dir=soft_dir,
    )
    expr_csv = soft_res["expression_matrix_csv"]
    sg_csv   = soft_res["sample_groups_csv"]
    ann_csv  = soft_res["annotation_csv"]
    print(f"  expr={expr_csv}\n  sg={sg_csv}\n  ann={ann_csv}")

    nl_task = f"""我有一份 GEO GDS6016 的小鼠脑组织微阵列数据，已经预先用 gds_soft_parser 解析成 3 个 CSV：
  - expression_matrix_csv = {expr_csv}   (probe 级表达矩阵, 41282 个探针 × 6 个样本)
  - sample_groups_csv     = {sg_csv}     (6 个样本，分两组：'En2 wildtype' 和 'En2 knockout')
  - annotation_csv        = {ann_csv}    (探针↔基因映射)

请按 GEO2R 的标准做法做差异表达 + 通路分析，**严格使用以下 3 个 solver、不要替换**：

  step 1 — `limma_deg_two_group`  (from='initial')
       直接在 probe 级表达矩阵上做 limma 差异表达。**不要先做 probe_to_gene_collapse**！
       mapping:
         gene_matrix_csv   = {expr_csv}
         sample_groups_csv = {sg_csv}
         group_a           = "En2 wildtype"
         group_b           = "En2 knockout"
         moderation        = true
         group_field       = "group_description"

  step 2 — `probe_deg_collapse_to_gene`  (from='previous')
       注意 solver id 一定是 `probe_deg_collapse_to_gene`，不是 probe_to_gene_collapse；
       前者作用在 DEG 表上（按 min adj_p 取每个基因最佳 probe），后者作用在表达矩阵上。
       mapping:
         deg_table_csv  = (来自 step 1 的 deg_table.csv)
         annotation_csv = {ann_csv}
         drop_unmapped  = true

  step 3 — `pathway_enrichment_fisher`  (from='step', step_index=1, csv_key='gene_deg_table_csv')
       mapping:
         deg_table_csv = (来自 step 2 的 gene_deg_table.csv)
         top_k         = 200
         case_insensitive = true

只要 3 个 solver，按上面顺序输出 JSON。"""
    print()
    print("[demo] -------- NL task to LLM planner --------")
    for line in nl_task.splitlines():
        print("  | " + line)
    print("[demo] -----------------------------------------")

    res = solve_task(
        task=nl_task,
        csv_path=Path(sg_csv),
        output_dir=run_dir / "agent_run",
        run_id="agent",
        use_llm_mapping=True,
        # qwen3-8b sometimes drops explicit `mapping:` blocks from a
        # long NL task.  Pin the environment-specific paths + the
        # group labels here so the LLM still owns the solver chain
        # but cannot break composability.  Per-step, by index.
        step_overrides=[
            # step 1 — limma on probe-level expression matrix
            {
                "gene_matrix_csv":   str(expr_csv),
                "sample_groups_csv": str(sg_csv),
                "group_a":           "En2 wildtype",
                "group_b":           "En2 knockout",
                "moderation":        True,
                "group_field":       "group_description",
            },
            # step 2 — collapse probe-DEG to gene-DEG
            {
                "annotation_csv": str(ann_csv),
                "drop_unmapped":  True,
            },
            # step 3 — pathway enrichment
            {
                "top_k":            200,
                "case_insensitive": True,
            },
        ],
    )

    print()
    print("=" * 78)
    print(f"[demo] agent verdict: ok={res.ok}  error={res.error}")
    print("=" * 78)
    plan = getattr(res, "plan", None)
    if plan and getattr(plan, "spec", None):
        print(f"plan rationale: {plan.rationale}")
        print("planned solvers:")
        for s in plan.spec.get("steps", []):
            print(f"  - {s.get('solver'):<32s} from={s.get('from'):<10s} "
                  f"mapping_keys={list((s.get('mapping') or {}).keys())}")
    print()
    print("runtime per-step records:")
    gene_deg_csv = None
    for r in (res.steps or []):
        outs = list((r.get("outputs") or {}).keys())
        print(f"  - {r['name']:<40s} {r['solver']:<28s} "
              f"src={r.get('mapping_source','?'):<8s} status={r.get('status')} "
              f"outputs={outs}")
        if r.get("status") == "error":
            print(f"      ERROR: {r.get('error')}")
        for k, v in (r.get("outputs") or {}).items():
            if k == "gene_deg_table_csv" and isinstance(v, str):
                gene_deg_csv = Path(v)

    if gene_deg_csv is None or not gene_deg_csv.is_file():
        print()
        print("[demo] no gene_deg_table.csv produced; skipping comparison")
        return

    # ---------- compare to GEO2R (apples to apples) ----------
    print()
    print("=" * 78)
    print("[demo] gene-level comparison vs GEO2R (BOTH collapsed by min adj_p)")
    print("=" * 78)
    geo_df = _collapse_geo2r_tsv()
    cmp = _compare(gene_deg_csv, geo_df)
    print(f"shared genes               : {cmp['n_shared']}")
    print(f"Spearman ρ on -log10(adj_p): {cmp['spearman_neg_log10_adj_p']:.4f}")
    print(f"|Spearman ρ| on logFC      : {cmp['abs_spearman_logFC']:.4f}")
    for K, j in cmp["topk_jaccard"].items():
        print(f"top-{K:>4d} jaccard          : {j:.4f}")
    print()
    print(f"agent  top 10 = {cmp['agent_top10']}")
    print(f"GEO2R  top 10 = {cmp['geo2r_top10']}")

    summary = {"comparison": cmp,
                "manifest": str(run_dir / "agent_run" / "agent" / "manifest.json")}
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"[demo] full summary at {run_dir/'summary.json'}")


if __name__ == "__main__":
    main()
