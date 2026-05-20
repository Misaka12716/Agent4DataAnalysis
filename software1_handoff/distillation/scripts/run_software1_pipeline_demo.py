"""End-to-end pipeline demos.

Two pipelines are run, each chaining the primary csv output of one
solver into the next:

  Pipeline 1  (PANSS workflow, 3 steps):
    panss_factor_score → normality_test → multiple_correction

  Pipeline 2  (clinical lab EDA, 4 steps):
    missing_summary → fillna_median → outlier_iqr_flag → pearson_correlation

Both ought to finish with overall_ok = True; the report records every
step's input csv, mapping source, and produced artifacts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from distillation.software1_solver import (
    Pipeline, PipelineStep,
)
from distillation.software1_solver.solvers import (
    panss_factor_score,
    normality_test,
    multiple_correction,
    data_governance,
    correlation,
)


BENCH = ROOT / "benchmark" / "Software1_Bench"
PIPE_BASE = ROOT / "_solver_outputs"

PANSS_INPUT_CSV = (BENCH / "F14_scale_structuring_extraction"
                          / "selfcon_panss_item_to_total"
                          / "inputs" / "panss_items.csv")
LAB_INPUT_CSV = (BENCH / "F13_outlier_reference_range_detection"
                       / "selfcon_reference_range_audit"
                       / "inputs" / "lab_panel.csv")

LAB_NUMERIC = ["WBC_10e9_per_L", "Hemoglobin_g_per_L",
               "Platelet_10e9_per_L", "ALT_U_per_L",
               "Creatinine_umol_per_L", "Sodium_mmol_per_L",
               "Potassium_mmol_per_L", "Glucose_fasting_mmol_per_L"]


PIPELINE_PANSS = Pipeline([
    PipelineStep(
        name="step1_score_panss",
        solver=panss_factor_score.get_solver(),
        mapping_override={
            "id_col": "PatientID",
            "time_col": "VisitWeek",
            "positive_items": [f"P{i}" for i in range(1, 8)],
            "negative_items": [f"N{i}" for i in range(1, 8)],
            "general_items":  [f"G{i}" for i in range(1, 17)],
        },
    ),
    PipelineStep(
        name="step2_normality_on_scores",
        solver=normality_test.get_solver(alpha=0.05),
        input_from="step1_score_panss",
        input_output_key="scored_csv",
        mapping_override={
            "test_columns": ["Positive_score", "Negative_score",
                              "General_score", "Total_score"],
        },
    ),
    PipelineStep(
        name="step3_fdr_on_normality_pvalues",
        solver=multiple_correction.get_solver(alpha=0.05),
        input_from="step2_normality_on_scores",
        input_output_key="results_csv",
        mapping_override={
            "test_id_col": "column",
            "p_value_col": "shapiro_p",
        },
    ),
])


PIPELINE_LAB = Pipeline([
    PipelineStep(
        name="step1_missing_summary",
        solver=data_governance.get_missing_summary_solver(),
        mapping_override={},
    ),
    PipelineStep(
        name="step2_fillna_median",
        # consumes the original lab csv, NOT step1's summary
        solver=data_governance.get_fillna_median_solver(),
        input_from="__initial__",
        mapping_override={"numeric_columns": LAB_NUMERIC},
    ),
    PipelineStep(
        name="step3_iqr_outlier_flag",
        solver=data_governance.get_outlier_iqr_solver(k=1.5),
        # consume filled (median-imputed) table from step2
        input_from="step2_fillna_median",
        input_output_key="filled_csv",
        mapping_override={"id_col": "PatientID",
                           "numeric_columns": LAB_NUMERIC},
    ),
    PipelineStep(
        name="step4_pearson_corr_on_filled",
        solver=correlation.get_pearson_solver(),
        input_from="step2_fillna_median",
        input_output_key="filled_csv",
        mapping_override={"numeric_columns": LAB_NUMERIC},
    ),
])


def _run_and_report(pipeline, initial_csv, output_dir, label):
    print(f"\n{'=' * 60}\n{label}\ninitial input: {initial_csv.name}\n"
          f"{'=' * 60}")
    out_dir = PIPE_BASE / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    result = pipeline.run(initial_input_csv=initial_csv, output_dir=out_dir)
    print(f"pipeline ok = {result.ok}")
    for s in result.steps:
        tag = "✓" if s["status"] == "ok" else "✗"
        print(f"  {tag} {s['name']:<35s} solver={s['solver']}")
        print(f"     input  = {Path(s['input_csv']).name}")
        if s["status"] != "ok":
            print(f"     ERROR: {s['error']}")
            continue
        for k, v in s["outputs"].items():
            if isinstance(v, str) and v.endswith((".csv", ".json")):
                size = Path(v).stat().st_size
                print(f"     output[{k}] = {Path(v).name} ({size} bytes)")
            elif isinstance(v, dict):
                inline = json.dumps(v, ensure_ascii=False, default=str)
                if len(inline) > 200:
                    inline = inline[:200] + "..."
                print(f"     output[{k}] = {inline}")
            else:
                print(f"     output[{k}] = {v}")
    return result


def _md_for_pipeline(label, result):
    md = [f"## {label}", "",
          f"- overall_ok = **{result.ok}**", ""]
    for s in result.steps:
        tag = "PASS" if s["status"] == "ok" else "FAIL"
        md.append(f"### {s['name']} — {tag}")
        md.append(f"- solver: `{s['solver']}`")
        md.append(f"- input csv: `{Path(s['input_csv']).name}`")
        md.append(f"- mapping ({s['mapping_source']}): "
                  f"`{json.dumps(s['mapping'], ensure_ascii=False, default=str)}`")
        if s["status"] == "ok":
            for k, v in s["outputs"].items():
                if isinstance(v, str) and v.endswith((".csv", ".json")):
                    md.append(f"- produced `{k}` → `{Path(v).name}`")
                elif isinstance(v, dict):
                    md.append(f"- `{k}` (dict, keys): "
                              f"`{list(v.keys())[:8]}`")
                else:
                    md.append(f"- `{k}` = `{v}`")
        else:
            md.append(f"- **error**: {s['error']}")
        md.append("")
    return md


def main():
    r1 = _run_and_report(
        PIPELINE_PANSS, PANSS_INPUT_CSV,
        "pipeline_panss_normality_fdr",
        label="PIPELINE 1 — PANSS items → factor scores → normality "
              "→ multiple correction (3 steps)",
    )
    r2 = _run_and_report(
        PIPELINE_LAB, LAB_INPUT_CSV,
        "pipeline_lab_eda",
        label="PIPELINE 2 — lab panel → missing_summary → fillna_median "
              "→ IQR outlier flag → Pearson correlation (4 steps)",
    )

    md = ["# Software1_Solver — pipeline demo report", "",
          f"Two demo pipelines run; both must end with overall_ok = True "
          "to prove the composability contract works.", ""]
    md += _md_for_pipeline(
        "Pipeline 1 — PANSS items → factor scores → normality → "
        "multiple correction (3 steps)", r1)
    md += _md_for_pipeline(
        "Pipeline 2 — lab panel → missing_summary → fillna_median → "
        "IQR outlier flag → Pearson correlation (4 steps)", r2)

    out_md = BENCH / "pipeline_demo_report.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"\nwrote {out_md}")


if __name__ == "__main__":
    main()
