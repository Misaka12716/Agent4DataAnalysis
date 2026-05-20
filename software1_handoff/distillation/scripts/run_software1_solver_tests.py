"""Run the v2 Software 1 reference solvers against their benchmark
tasks and report strict-GT comparisons.

This is the "ground-truth gate" the user asked for: every solver is
verified end-to-end against the task's GT csv/json with deterministic
expectations (固定 seed where applicable).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from distillation.software1_solver import (
    Role,
    run_task,
    map_columns_rule_based,
    profile_df,
)
from distillation.software1_solver.contract import ColumnMapping
from distillation.software1_solver.solvers import (
    panss_factor_score,
    reference_range_flag,
    normality_test,
    multiple_correction,
    logistic_regression,
    cox_regression,
    association_rules,
    metadata_parser,
    panss_trajectory_responder,
    svm_classifier,
    knn_classifier,
    text_features,
)


BENCH = ROOT / "benchmark" / "Software1_Bench"
TMP = ROOT / "_solver_outputs"
TMP.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Test cases:  (task_dir, csv_name, solver_factory, gt_checks, override)
# ---------------------------------------------------------------------------
def _panss_override():
    """PANSS items P1..P7 / N1..N7 / G1..G16 — the rule-based mapper
    can't easily partition them because they all have the same dtype.
    We therefore provide an explicit mapping; in production this is
    exactly the kind of partition an LLM would do given the column
    names + a hint."""
    return {
        "id_col": "PatientID",
        "time_col": "VisitWeek",
        "positive_items": [f"P{i}" for i in range(1, 8)],
        "negative_items": [f"N{i}" for i in range(1, 8)],
        "general_items":  [f"G{i}" for i in range(1, 17)],
    }


def _refrange_static_params() -> Dict[str, Dict[str, float]]:
    return {
        "WBC_10e9_per_L":              {"low": 4.0,   "high": 10.0},
        "Hemoglobin_g_per_L":          {"low": 110,   "high": 160},
        "Platelet_10e9_per_L":         {"low": 100,   "high": 300},
        "ALT_U_per_L":                 {"low": 0,     "high": 40},
        "Creatinine_umol_per_L":       {"low": 53,    "high": 106},
        "Sodium_mmol_per_L":           {"low": 135,   "high": 145},
        "Potassium_mmol_per_L":        {"low": 3.5,   "high": 5.5},
        "Glucose_fasting_mmol_per_L":  {"low": 3.9,   "high": 6.1},
    }


CASES: List[Dict[str, Any]] = [
    {
        "name": "PANSS factor sum",
        "task_dir": BENCH / "F14_scale_structuring_extraction"
                          / "selfcon_panss_item_to_total",
        "input_csv": "panss_items.csv",
        "solver": panss_factor_score.get_solver(),
        "override": _panss_override(),
        "checks": [{
            "kind": "csv_exact",
            "actual_key": "scored_csv",
            "gt_path": "gt/panss_scored.csv",
        }],
    },
    {
        "name": "Reference-range flag",
        "task_dir": BENCH / "F13_outlier_reference_range_detection"
                          / "selfcon_reference_range_audit",
        "input_csv": "lab_panel.csv",
        "solver": reference_range_flag.get_solver(_refrange_static_params()),
        # rule-based mapper handles this:
        # id_col -> PatientID; lab_columns -> all numeric columns
        "override": None,
        "checks": [{
            "kind": "csv_exact",
            "actual_key": "flags_csv",
            "gt_path": "gt/lab_flags.csv",
        }],
    },
    {
        "name": "Normality tests",
        "task_dir": BENCH / "F02_descriptive_stats_distribution"
                          / "selfcon_normality_test_panel",
        "input_csv": "lab_values.csv",
        "solver": normality_test.get_solver(alpha=0.05),
        "override": None,
        "checks": [
            {
                "kind": "json_assertions",
                "actual_key": "stats_dict",
                "gt_path": "gt/normality_truth.json",
            },
        ],
    },
    {
        "name": "Multiple-comparison correction (Bonferroni + BH-FDR)",
        "task_dir": BENCH / "F04_group_difference_hypothesis_test"
                          / "selfcon_multiple_correction_fdr",
        "input_csv": "pvalues.csv",
        "solver": multiple_correction.get_solver(alpha=0.05),
        "override": None,
        "checks": [{
            "kind": "json_assertions",
            "actual_key": "summary_dict",
            "gt_path": "gt/correction_truth.json",
        }],
    },
    {
        "name": "Self-harm risk classifier (LR + 5-fold CV)",
        "task_dir": BENCH / "F06_supervised_classification"
                          / "selfcon_self_harm_risk_classifier",
        "input_csv": "self_harm_features.csv",
        "solver": logistic_regression.get_solver(
            random_state=42, cv_folds=5,
            external_label_csv=str(
                BENCH / "F06_supervised_classification"
                      / "selfcon_self_harm_risk_classifier"
                      / "gt" / "labels.csv"
            ),
        ),
        # NOTE: rule-based mapper would see `on_antidepressant` (0/1) and
        # mis-classify it as the target.  In production the LLM mapper
        # (or a Software 1 user) would resolve this — here we provide an
        # explicit override that lists the actual 5 predictors and
        # marks target_col=None (label arrives via external_label_csv).
        "override": {
            "id_col": "PatientID",
            "feature_columns": [
                "HAMD17_total", "Beck_hopelessness_total",
                "sleep_hours", "n_prior_self_harm_attempts",
                "on_antidepressant",
            ],
            # target_col deliberately omitted — comes from external GT
        },
        "checks": [{
            "kind": "metric_threshold",
            "actual_key": "metrics_dict",
            "gt_path": "gt/labels.csv",  # not used; thresholds inline
            "thresholds": {
                # task prompt: "目标：5 折 CV AUROC ≥ 0.78"
                "auroc": ">= 0.78",
            },
        }],
    },
    {
        "name": "Cox PH (psych readmission)",
        "task_dir": BENCH / "F08_survival_analysis"
                          / "selfcon_cox_psych_readmission",
        "input_csv": "psych_readmission_survival.csv",
        "solver": cox_regression.get_solver(penalizer=0.001),
        "override": {
            "id_col":       "PatientID",
            "time_col":     "time_days",
            "event_col":    "event_readmitted",
            "covariates":   ["Age", "is_male", "panss_baseline",
                              "n_prior_admissions",
                              "on_long_acting_injectable"],
            "stratify_col": "on_long_acting_injectable",
        },
        "checks": [{
            "kind": "json_assertions",
            "actual_key": "metrics_dict",
            "gt_path": "gt/cox_truth.json",
        }],
    },
    {
        "name": "ADR association rules (FP-Growth)",
        "task_dir": BENCH / "F12_association_comorbidity_pattern"
                          / "selfcon_adr_association_rules",
        "input_csv": "drug_event_records.csv",
        "solver": association_rules.get_solver(min_support=0.05,
                                                min_confidence=0.3),
        "override": {
            "items_col":   "drugs",
            "targets_col": "events",
        },
        "checks": [{
            "kind": "association_rules",
            "actual_key": "rules_df",
            "gt_path": "gt/adr_truth.json",
        }],
    },
    {
        "name": "Metadata auto-parser",
        "task_dir": BENCH / "F01_data_governance_cleaning"
                          / "selfcon_metadata_auto_parser",
        "input_csv": "clinical_table.csv",
        "solver": metadata_parser.get_solver(sample_topk=3),
        "override": {},  # whole-DF solver, no role mapping
        "checks": [{
            "kind": "metadata_schema",
            "actual_key": "metadata_dict",
            "gt_path": "gt/expected_schema.json",
        }],
    },
    {
        "name": "PANSS trajectory responder",
        "task_dir": BENCH / "F10_time_series_followup"
                          / "selfcon_panss_trajectory_responder",
        "input_csv": "panss_total_followup.csv",
        "solver": panss_trajectory_responder.get_solver(
            responder_threshold_pct=30.0),
        "override": {
            "id_col":       "PatientID",
            "baseline_col": "Total_0w",
            "endpoint_col": "Total_12w",
        },
        "checks": [{
            "kind": "csv_exact",
            "actual_key": "trajectory_csv",
            "gt_path": "gt/panss_trajectory_gt.csv",
        }],
    },
    {
        "name": "SVM RBF classifier (5-fold CV + GridSearchCV)",
        "task_dir": BENCH / "F06_supervised_classification"
                          / "selfcon_svm_rbf_classifier",
        "input_csv": "svm_features.csv",
        "solver": svm_classifier.get_solver(
            random_state=42, cv_folds=5,
            external_label_csv=str(
                BENCH / "F06_supervised_classification"
                      / "selfcon_svm_rbf_classifier"
                      / "gt" / "labels.csv"
            ),
        ),
        # Need to skip on_antidepressant-style feature confusion if any;
        # for SVM 8-feat input is clean, rule-based mapper works.
        "override": None,
        "checks": [{
            "kind": "metric_threshold",
            "actual_key": "metrics_dict",
            "gt_path": "gt/labels.csv",
            "thresholds": {"auroc": ">= 0.85"},
        }],
    },
    {
        "name": "KNN K-selection (3-class)",
        "task_dir": BENCH / "F06_supervised_classification"
                          / "selfcon_knn_k_selection",
        "input_csv": "knn_features.csv",
        "solver": knn_classifier.get_solver(
            random_state=42, cv_folds=5,
            external_label_csv=str(
                BENCH / "F06_supervised_classification"
                      / "selfcon_knn_k_selection"
                      / "gt" / "labels.csv"
            ),
        ),
        "override": None,
        "checks": [{
            "kind": "metric_threshold",
            "actual_key": "metrics_dict",
            "gt_path": "gt/labels.csv",
            "thresholds": {"cv_accuracy": ">= 0.85"},
        }],
    },
    {
        "name": "Text features (Transformer/TF-IDF)",
        "task_dir": BENCH / "F09_dimensionality_reduction_features"
                          / "selfcon_text_features_transformer",
        "input_csv": "psych_phrases.csv",
        "solver": text_features.get_solver(),
        "override": {
            "id_col":    "phrase_id",
            "text_col":  "text_zh",
            "label_col": "label",
        },
        "checks": [{
            "kind": "metric_threshold",
            "actual_key": "manifest_dict",
            "gt_path": "gt/text_features_truth.json",
            "thresholds": {"label_consistency_z": ">= 1.5"},
        }],
    },
]


def main() -> None:
    results: List[Dict[str, Any]] = []
    for case in CASES:
        out_dir = TMP / case["task_dir"].name
        out_dir.mkdir(exist_ok=True)
        print(f"\n=== {case['name']} ===")
        r = run_task(
            task_dir=case["task_dir"],
            input_csv_name=case["input_csv"],
            solver=case["solver"],
            gt_checks=case["checks"],
            output_dir=out_dir,
            override_mapping=case["override"],
        )
        r["case_name"] = case["name"]
        results.append(r)

        # short-format console output
        print(f"  mapping ({r['mapping']['source']}):")
        for k, v in r["mapping"]["mapping"].items():
            print(f"    {k}: {v}")
        run_status = r["run"]["status"]
        if run_status != "ok":
            print(f"  RUN FAILED: {r['run'].get('error')}")
        else:
            for chk in r["checks"]:
                tag = "✓" if chk["match"] else "✗"
                print(f"  {tag} {chk['kind']:<18s} -> {chk['summary']}")
                if not chk["match"] and chk.get("details"):
                    for d in chk["details"][:5]:
                        print(f"      - {d}")
        print(f"  overall_ok = {r['ok']}")

    # ----- write report -----
    md: list[str] = []
    md += [
        "# Software1_Solver v2 — strict GT verification report",
        "",
        "## TL;DR",
        "",
        f"- **{sum(1 for r in results if r['ok'])}/{len(results)} solvers pass strict ground-truth comparison.**",
        "- Every Software 1 capability under test is implemented as a "
        "library-backed *reference solver* (pandas / scipy / sklearn / "
        "statsmodels / lifelines / mlxtend).  The csv-copied operators "
        "are explicitly abandoned wherever they are missing or buggy.",
        "- Solvers do **not** hardcode user column names.  Each declares a "
        "role-based contract (`Role.ID`, `Role.NUMERIC_LIST`, "
        "`Role.BINARY_TARGET`, ...).  The mapper resolves these to "
        "actual column names — the same plug-in API works for "
        "rule-based, manual override, or LLM mappers.",
        "",
        "## What the strict-GT comparison means per solver",
        "",
        "| solver | capability | input rows | mapping source | "
        "comparison kind | result |",
        "|---|---|---|---|---|---|",
    ]
    capability_map = {r["solver"]: r["case_name"] for r in results}
    for r in results:
        task = Path(r["task_dir"]).name
        src = r["mapping"]["source"]
        n = r["profile"]["shape"][0]
        kinds = ", ".join({c["kind"] for c in r["checks"]})
        gt_match = "PASS" if r["ok"] else "FAIL"
        md.append(f"| `{r['solver']}` | {task} | {n} | {src} | {kinds} | "
                  f"**{gt_match}** |")

    md += [
        "",
        "## Strict-GT comparison — what \"strict\" actually means",
        "",
        "Every task is treated as a deterministic computation; "
        "stochastic ML uses a fixed `random_state`.  The comparator "
        "library covers four sub-types:",
        "",
        "1. **`csv_exact`** — column order, row order, dtypes, and every "
        "value must match.  Numerics use `atol=1e-9`.  Used for PANSS "
        "factor sum and reference-range flag, where the GT csv is the "
        "ground truth itself.",
        "2. **`csv_numeric_tol`** — same as above with a configurable "
        "abs/rel tolerance for numerics.  Available when GT is a "
        "reference-impl-produced csv where rounding may differ.",
        "3. **`json_assertions`** — recurses through the GT json and "
        "evaluates every leaf.  Supported leaf encodings: equality "
        "(numeric ≈, bool ==, str case-insensitive ==, list = set), "
        "and threshold suffixes `_lt` / `_lte` / `_gt` / `_gte` / "
        "`_min` / `_max`.  Documentation-only keys "
        "(`interpretation`, `scoring_metric`, `flagging_rule`, "
        "...) are skipped to avoid false negatives.  Used for "
        "normality, multiple correction, Cox.",
        "4. **`metric_threshold`** — for ML tasks where the GT is a "
        "label csv held out of the input.  Solver reports a metrics "
        "dict; comparator checks each metric against an inline "
        "threshold expression like `\"auroc\": \">= 0.78\"`.",
        "5. **`association_rules`** — for FP-Growth-style outputs.  "
        "Each expected rule "
        "(`{antecedent, consequent, confidence_min}`) is recovered "
        "from the solver's rules table.",
        "",
        "## Per-solver detail",
        "",
    ]
    for r in results:
        md.append(f"### `{r['solver']}` — {r['case_name']}")
        md.append(f"- task = `{Path(r['task_dir']).name}`  "
                  f"({r['profile']['shape'][0]} rows × "
                  f"{r['profile']['shape'][1]} cols)")
        md.append(f"- mapping ({r['mapping']['source']}):")
        for k, v in r["mapping"]["mapping"].items():
            md.append(f"  - `{k}` → `{v}`")
        for chk in r["checks"]:
            tag = "PASS" if chk["match"] else "FAIL"
            md.append(f"- `{chk['kind']}` → **{tag}**: {chk['summary']}")
            if not chk["match"]:
                for d in chk.get("details", [])[:5]:
                    md.append(f"  - {d}")
        md.append("")

    md += [
        "## How to extend / re-use",
        "",
        "Add a new solver in three steps:",
        "",
        "1. Drop a file in `distillation/software1_solver/solvers/<name>.py` "
        "with a module-level `CONTRACT: SolverContract` and a `class "
        "<Name>Solver` exposing `run(df, mapping, output_dir) -> dict`.",
        "2. Pick the most direct standard-library implementation — "
        "scipy / sklearn / statsmodels / lifelines / mlxtend / "
        "torch.  This *is* the new operator; the original csv "
        "operator no longer matters.",
        "3. Add a case to `distillation/scripts/run_software1_solver_tests.py` "
        "linking the solver to its task csv and the `gt/...` files. "
        "Pick the right comparator kind for the GT shape.",
        "",
        "## LLM mapper integration point",
        "",
        "The mapper API is:",
        "",
        "```python",
        "from distillation.software1_solver import map_columns_llm",
        "from distillation.software1_solver.profiler import profile_df",
        "",
        "profile = profile_df(df)              # compact, ~600 token",
        "mapping = map_columns_llm(profile, solver.contract, llm)",
        "# llm: Callable[[str], str]  — pass any callable that takes a",
        "# prompt and returns a JSON string {role_key: column_name(s)}.",
        "```",
        "",
        "The runner (`run_task`) accepts `llm=` directly; if the LLM "
        "returns invalid JSON or empty mapping it transparently falls "
        "back to the rule-based mapper.  In production wire `llm` to "
        "your OpenAI / Claude / local-Ollama call — the rest of the "
        "stack does not change.",
        "",
        "## Files",
        "",
        "- `distillation/software1_solver/__init__.py` — public surface.",
        "- `.../profiler.py` — `profile_df`, `profile_to_text`.",
        "- `.../contract.py` — `Role`, `RoleSpec`, `SolverContract`, "
        "`ColumnMapping`.",
        "- `.../mapper.py` — `map_columns_rule_based`, "
        "`map_columns_llm`.",
        "- `.../comparator.py` — `compare_csv_exact`, "
        "`compare_csv_numeric_tol`, `compare_json_with_assertions`, "
        "`compare_association_rules`.",
        "- `.../runner.py` — `run_task` (end-to-end orchestrator).",
        "- `.../solvers/` — 7 reference solvers (one per file).",
        "- `distillation/scripts/run_software1_solver_tests.py` — "
        "this report's source.",
        "",
    ]

    out_md = BENCH / "solver_qa_report_v2.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"\nwrote {out_md}")

    # also dump full json for downstream consumption
    out_json = BENCH / "solver_qa_report_v2.json"
    import pandas as _pd

    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, _pd.DataFrame):
            return f"<DataFrame shape={list(o.shape)}>"
        if isinstance(o, _pd.Series):
            return f"<Series len={len(o)}>"
        # numpy scalars
        if hasattr(o, "item") and not isinstance(o, (list, dict, str)):
            try:
                return o.item()
            except Exception:
                return str(o)
        return o
    out_json.write_text(json.dumps(_clean(results), ensure_ascii=False,
                                    indent=2, default=str), encoding="utf-8")
    print(f"wrote {out_json}")


if __name__ == "__main__":
    main()
