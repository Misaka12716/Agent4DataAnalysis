"""End-to-end smoke for the LLM agent.

Drive the planner with realistic natural-language tasks against
real CSVs from Software1_Bench, then verify the plan was executable
and produced files.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from distillation.software1_agent.agent import solve_task


SCENARIOS = [
    {
        "name": "T1_lab_correlations",
        "task": ("我有一份成人化验数据。请先检查每列缺失情况，然后用中位数补齐缺失，"
                  "最后给出各项化验指标之间两两相关系数（Pearson 即可），"
                  "并指出最相关的几对。"),
        "csv": "benchmark/Software1_Bench/F13_outlier_reference_range_detection/"
               "selfcon_reference_range_audit/inputs/lab_panel.csv",
    },
    {
        "name": "T2_panss_factors",
        "task": ("这是 PANSS 30 项条目的随访数据。请把它聚合成阳性、阴性、一般精神病理"
                  "三个因子分以及 PANSS 总分，每行一次访视。"),
        "csv": "benchmark/Software1_Bench/F14_scale_structuring_extraction/"
               "selfcon_panss_item_to_total/inputs/panss_items.csv",
    },
    {
        "name": "T3_lab_reference_range_audit",
        "task": ("请基于成人参考区间评估每个病人每项化验值是否异常，"
                  "输出每个 (病人, 指标) 的 status (low/normal/high) 长表。"),
        "csv": "benchmark/Software1_Bench/F13_outlier_reference_range_detection/"
               "selfcon_reference_range_audit/inputs/lab_panel.csv",
    },
    {
        "name": "T4_normality_then_correction",
        "task": ("先对所有数值列做正态性检验，再对得到的 p 值做 BH-FDR 多重比较校正。"),
        "csv": "benchmark/Software1_Bench/F13_outlier_reference_range_detection/"
               "selfcon_reference_range_audit/inputs/lab_panel.csv",
    },
    {
        "name": "T5_cox_psych_readmission",
        "task": ("这是精神科再入院的随访数据，事件列是 readmitted_30d，时间列是 days_to_event"
                  "或类似名字。请用 Cox 比例风险模型估计每个候选协变量对再入院的风险比。"),
        "csv": "benchmark/Software1_Bench/F08_survival_analysis/"
               "selfcon_cox_psych_readmission/inputs/psych_readmission_survival.csv",
    },
]


def main():
    out_root = ROOT / "distillation" / "software1_agent" / "_runs"
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"{'scenario':<32} ok  steps  source(s)              run_id")
    print("-" * 110)

    summary = []
    for sc in SCENARIOS:
        csv = ROOT / sc["csv"]
        if not csv.is_file():
            print(f"{sc['name']:<32} SKIP  csv missing: {csv}")
            continue
        try:
            res = solve_task(task=sc["task"], csv_path=csv,
                              output_dir=out_root, use_llm_mapping=True)
        except Exception as e:
            print(f"{sc['name']:<32} ERR    {type(e).__name__}: {e}")
            continue
        ok = "✓" if res.ok else "✗"
        srcs = sorted({s.get("mapping_source", "?") for s in res.steps})
        print(f"{sc['name']:<32}  {ok}    {len(res.steps):<2}    "
              f"{','.join(srcs):<22} {res.run_dir.name}")
        summary.append({
            "name": sc["name"],
            "ok": res.ok,
            "rationale": res.plan.rationale,
            "n_steps": len(res.steps),
            "step_solvers": [s.get("solver") for s in res.steps],
            "step_status": [s.get("status") for s in res.steps],
            "errors": [s.get("error") for s in res.steps if s.get("status") != "ok"],
            "run_dir": str(res.run_dir),
        })

    print()
    print("=" * 110)
    for s in summary:
        print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()
