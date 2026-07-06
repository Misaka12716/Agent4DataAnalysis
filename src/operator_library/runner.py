"""End-to-end task runner.

For each registered (task_id, solver) pair this:
  1. Loads the input csv from the benchmark folder.
  2. Profiles the DataFrame.
  3. Maps column-roles (rule-based; falls back to LLM if provided).
  4. Runs the solver.
  5. Compares the produced output(s) against the task's GT files.
  6. Returns a structured result dict.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol

import pandas as pd

from .contract import ColumnMapping, SolverContract
from .mapper import LLMCallable, map_columns_llm, map_columns_rule_based
from .profiler import profile_df


class Solver(Protocol):
    contract: SolverContract

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        ...


def run_task(*,
             task_dir: Path,
             input_csv_name: str,
             solver: Solver,
             gt_checks: list,
             output_dir: Path,
             llm: Optional[LLMCallable] = None,
             override_mapping: Optional[Dict[str, Any]] = None,
             ) -> Dict[str, Any]:
    """Run one solver against one task and run all GT checks.

    Parameters
    ----------
    task_dir : Path
        e.g. benchmark/Software1_Bench/F14_*/selfcon_panss_item_to_total/
    input_csv_name : str
        relative to task_dir/inputs/, e.g. "panss_items.csv"
    solver : Solver
        with .contract and .run()
    gt_checks : list of dicts, each with keys
        - "kind": "csv_exact" | "csv_numeric_tol" | "json_assertions"
        - "actual_key": which output_dict key holds the produced path/dict
        - "gt_path": path under task_dir to the GT
        - optional "atol"/"rtol"
    output_dir : Path
        where the solver writes deliverables
    llm : optional LLMCallable
        if provided, used as primary mapper
    override_mapping : optional dict
        manual column mapping; bypasses the auto-mapper

    Returns
    -------
    dict with keys task_id, solver, profile, mapping, run, checks, ok.
    """
    task_dir = Path(task_dir)
    input_csv = task_dir / "inputs" / input_csv_name
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_csv)
    profile = profile_df(df)

    if override_mapping is not None:
        mapping = ColumnMapping(mapping=dict(override_mapping),
                                rationale="manual override",
                                source="manual")
    elif llm is not None:
        mapping = map_columns_llm(profile, solver.contract, llm)
        # if LLM mapping is empty, fall back to rule-based
        if not mapping.mapping:
            mapping = map_columns_rule_based(profile, solver.contract)
    else:
        mapping = map_columns_rule_based(profile, solver.contract)

    run_result: Dict[str, Any] = {"status": "init"}
    try:
        outputs = solver.run(df=df, mapping=mapping, output_dir=output_dir)
        run_result = {"status": "ok", "outputs": outputs}
    except BaseException as e:
        run_result = {
            "status": "run_error",
            "error": f"{type(e).__name__}: {e}",
        }

    # --- run GT checks ---
    check_results = []
    if run_result["status"] == "ok":
        outputs = run_result["outputs"]
        for chk in gt_checks:
            kind = chk["kind"]
            actual_key = chk["actual_key"]
            gt_path = task_dir / chk["gt_path"]

            actual = outputs.get(actual_key)
            if actual is None:
                check_results.append({
                    "kind": kind,
                    "actual_key": actual_key,
                    "gt": str(gt_path),
                    "match": False,
                    "summary": f"solver did not produce '{actual_key}'",
                })
                continue

            try:
                if kind == "csv_exact":
                    from .comparator import compare_csv_exact
                    res = compare_csv_exact(actual, gt_path,
                                            float_atol=chk.get("atol", 1e-9))
                elif kind == "csv_numeric_tol":
                    from .comparator import compare_csv_numeric_tol
                    res = compare_csv_numeric_tol(actual, gt_path,
                                                   atol=chk.get("atol", 1e-6),
                                                   rtol=chk.get("rtol", 1e-4))
                elif kind == "json_assertions":
                    from .comparator import compare_json_with_assertions
                    if isinstance(actual, dict):
                        actual_dict = actual
                    else:
                        actual_dict = json.loads(Path(actual).read_text(encoding="utf-8"))
                    gt_dict = json.loads(gt_path.read_text(encoding="utf-8"))
                    res = compare_json_with_assertions(actual_dict, gt_dict)
                elif kind == "association_rules":
                    from .comparator import compare_association_rules
                    gt_dict = json.loads(gt_path.read_text(encoding="utf-8"))
                    res = compare_association_rules(
                        actual,
                        gt_dict.get("expected_strong_rules", []),
                    )
                elif kind == "metadata_schema":
                    from .comparator import compare_metadata_schema
                    actual_dict = (actual if isinstance(actual, dict)
                                    else json.loads(Path(actual).read_text(
                                        encoding="utf-8")))
                    gt_dict = json.loads(gt_path.read_text(encoding="utf-8"))
                    res = compare_metadata_schema(actual_dict, gt_dict)
                elif kind == "metric_threshold":
                    # actual is a metrics dict, chk has thresholds
                    metrics = actual if isinstance(actual, dict) else \
                        json.loads(Path(actual).read_text(encoding="utf-8"))
                    threshold = chk["thresholds"]   # {metric: ">= 0.78"}
                    diffs = []
                    for m, expr in threshold.items():
                        if m not in metrics:
                            diffs.append(f"metric {m!r} missing in solver output")
                            continue
                        op_str, val_str = expr.split()
                        val = float(val_str)
                        v = float(metrics[m])
                        ops = {">=": v >= val, "<=": v <= val,
                               ">": v > val, "<": v < val,
                               "==": v == val}
                        if not ops.get(op_str, False):
                            diffs.append(f"{m}={v:.4f} fails {expr}")
                    res = {
                        "match": len(diffs) == 0,
                        "n_diffs": len(diffs),
                        "details": diffs,
                        "summary": "all metrics meet threshold"
                                   if not diffs else
                                   f"{len(diffs)} metrics below threshold",
                    }
                else:
                    res = {"match": False, "summary": f"unknown kind {kind}",
                           "n_diffs": 0, "details": []}
            except BaseException as e:
                res = {
                    "match": False,
                    "n_diffs": 0,
                    "details": [f"comparator raised {type(e).__name__}: {e}"],
                    "summary": f"comparator error: {e}",
                }
            res.update({"kind": kind, "actual_key": actual_key,
                        "gt": str(gt_path)})
            check_results.append(res)

    overall_ok = (run_result["status"] == "ok" and
                  all(c.get("match", False) for c in check_results))

    return {
        "task_dir": str(task_dir),
        "solver": solver.contract.name,
        "profile": profile,
        "mapping": {
            "source": mapping.source,
            "mapping": mapping.mapping,
            "rationale": mapping.rationale,
        },
        "run": run_result,
        "checks": check_results,
        "ok": overall_ok,
    }
