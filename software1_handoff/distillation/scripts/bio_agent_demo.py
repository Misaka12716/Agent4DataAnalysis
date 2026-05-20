"""End-to-end LLM-agent demo on GDS6016 bioinformatics data.

Flow:
  0. Pre-parse the SOFT file once via gds_soft_parser → known csv paths.
  1. Build a NL task that names the artifact paths.
  2. Hand the task + sample_groups.csv to ``software1_agent.solve_task``;
     the LLM planner picks bio operators from the catalog, fills the
     path PARAMS in each step's mapping, and the runner executes them.
  3. Print a compact verdict that compares the agent's DEG against the
     audit script's DEG (sanity check of "agent ran the right thing").

Output:  ``benchmark/Software1_Bench/real_medical_data/_agent_runs/<ts>``
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from distillation.software1_solver.contract import ColumnMapping
from distillation.software1_solver.solvers.bio import soft_parser as _bio_soft

from distillation.software1_pipeline_demo_app import llm_client
from distillation.software1_agent import solve_task


SOFT = (ROOT / "benchmark" / "Software1_Bench"
            / "real_medical_data" / "GDS6016_full.soft")
RUN_ROOT = (ROOT / "benchmark" / "Software1_Bench"
                / "real_medical_data" / "_agent_runs")


def _parse_soft_once(out_dir: Path) -> dict:
    """Parse SOFT to known paths.  Idempotent within the run dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    res = _bio_soft.get_solver(str(SOFT)).run(
        df=pd.DataFrame(),
        mapping=ColumnMapping({"soft_path": str(SOFT)}),
        output_dir=out_dir,
    )
    return {k: v for k, v in res.items()
            if isinstance(v, str) and v.endswith(".csv")}


def _build_task(artifacts: dict) -> str:
    em = artifacts["expression_matrix_csv"]
    sg = artifacts["sample_groups_csv"]
    an = artifacts["annotation_csv"]
    return (
        "我有一份 GEO GDS6016 的小鼠脑组织微阵列数据，已经预先用 "
        "gds_soft_parser 解析成 3 个 CSV：\n"
        f"  - expression_matrix_csv = {em}\n"
        f"  - sample_groups_csv     = {sg}\n"
        f"  - annotation_csv        = {an}\n"
        "你看到的这份输入 CSV 就是 sample_groups_csv，里面 6 个样本 "
        "分两组：'En2 wildtype' 是对照组 (group_a)，'En2 knockout' "
        "是处理组 (group_b)。请规划一条最短的分析管线，完成下面的全部 "
        "目标，并尽量复用上一步的输出：\n"
        "  1) 把探针级表达矩阵聚合到基因级（max 法）；\n"
        "  2) 在基因级表达矩阵上做 PCA（5 个主成分），目的是看两组是否 "
        "可分；\n"
        "  3) 对样本做层次聚类（用 ward + euclidean，n_clusters=2），"
        "看是否能正确还原两组；\n"
        "  4) 做两组差异表达（limma_deg_two_group），打开 EB 方差收缩，"
        "group_field 用 'group_description'；\n"
        "  5) 在 DEG 结果上跑 Hallmark 通路富集（Fisher / 超几何，top_k=200）。\n"
        "对每一步：solver 之间用 from='previous' 串起来；前 4 步都需要 "
        "在 mapping 里把上面的 csv 路径作为 PARAMS 提供给 solver；最后 "
        "一步 pathway_enrichment_fisher 接 limma 那一步的 deg_table_csv。"
    )


def _summarize_step(rec: dict, max_chars: int = 280) -> str:
    name = rec.get("name")
    solver = rec.get("solver")
    src = rec.get("mapping_source")
    status = rec.get("status")
    miss = rec.get("missing_required") or []
    err = rec.get("error")
    outs = list((rec.get("outputs") or {}).keys())
    line = (f"  - {name:30s} {solver:28s} src={src:8s}  status={status}"
            f"  outputs={outs}")
    if status != "ok":
        line += f"\n      ERROR: {err}"
    if miss:
        line += f"\n      missing: {miss}"
    if len(line) > max_chars:
        line = line[:max_chars] + "…"
    return line


def main():
    if not llm_client.is_available():
        print("[demo] LLM not configured (.env missing API key/base URL).")
        print("[demo] Cannot run agent demo — exiting.")
        return 1
    cfg = llm_client.get_config()
    print(f"[demo] LLM model = {cfg.model}  base_url = {cfg.base_url}")

    if not SOFT.is_file():
        print(f"[demo] SOFT file not found at {SOFT}")
        return 1

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = RUN_ROOT / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[demo] run_dir = {run_dir}")

    print("[demo] step 0: pre-parsing SOFT …")
    artifacts = _parse_soft_once(run_dir / "00_soft_parser")
    print(f"[demo]   expression_matrix_csv = {artifacts['expression_matrix_csv']}")
    print(f"[demo]   sample_groups_csv     = {artifacts['sample_groups_csv']}")
    print(f"[demo]   annotation_csv        = {artifacts['annotation_csv']}")

    task = _build_task(artifacts)
    print()
    print("[demo] -------- NL task to LLM planner --------")
    for line in task.splitlines():
        print(f"  | {line}")
    print("[demo] ----------------------------------------")
    print()

    print("[demo] handing off to software1_agent.solve_task …")
    res = solve_task(
        task=task,
        csv_path=Path(artifacts["sample_groups_csv"]),
        output_dir=run_dir / "agent_run",
        use_llm_mapping=True,
        planner_max_tokens=2400,
        run_id="agent",
    )
    print()
    print("=" * 78)
    print(f"[demo] agent verdict: ok={res.ok}  error={res.error}")
    print(f"[demo] manifest      : {res.manifest_path}")
    print(f"[demo] plan rationale: {res.plan.rationale}")
    print("=" * 78)
    print(f"[demo] planned solvers ({len(res.plan.spec.get('steps', []))}):")
    for s in res.plan.spec.get("steps", []):
        print(f"  - {s.get('solver')}   from={s.get('from')}   "
              f"mapping_keys={list((s.get('mapping') or {}).keys())}")
    print()
    print(f"[demo] runtime per-step records:")
    for rec in (res.steps or []):
        print(_summarize_step(rec))

    print()
    print(f"[demo] full manifest at {res.manifest_path}")

    # cross-check: if a deg_table.csv was produced, compare top genes
    # against the audit script's run for the same data.
    deg_path = None
    for rec in (res.steps or []):
        for k, v in (rec.get("outputs") or {}).items():
            if isinstance(v, str) and v.endswith("deg_table.csv"):
                deg_path = v
                break
    if deg_path and Path(deg_path).is_file():
        agent_deg = pd.read_csv(deg_path).sort_values("adj_p_value",
                                                        kind="mergesort")
        # find the latest audit run, if any
        audit_root = (ROOT / "benchmark" / "Software1_Bench"
                          / "real_medical_data" / "_audit_run")
        audit_runs = sorted(audit_root.glob("*/05_limma/deg_table.csv"))
        if audit_runs:
            audit_deg = pd.read_csv(audit_runs[-1]).sort_values(
                "adj_p_value", kind="mergesort")
            top_a = set(agent_deg.head(50)["gene_symbol"])
            top_b = set(audit_deg.head(50)["gene_symbol"])
            j = len(top_a & top_b) / max(1, len(top_a | top_b))
            print()
            print(f"[demo] agent vs audit (top-50 jaccard) = {j:.3f}  "
                  f"({len(top_a & top_b)} shared)")
            print(f"[demo]   agent top 5 = {agent_deg.head(5)['gene_symbol'].tolist()}")
            print(f"[demo]   audit top 5 = {audit_deg.head(5)['gene_symbol'].tolist()}")

    return 0 if res.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
