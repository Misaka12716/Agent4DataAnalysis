"""Software 1 reference solver framework.

Design (per user 2026-05-09):

1. **All Software 1 capabilities are deterministic computations** — given a
   fixed random seed, results must match GT bit-for-bit (or within strict
   numeric tolerance for floats).
2. **The original csv-copied operators are not authoritative**.  When a
   csv-copied operator is broken / missing / incomplete, we replace it with a
   solver written on top of standard libraries (pandas / scipy / sklearn /
   statsmodels / lifelines / mlxtend / etc.).  These library-backed solvers
   ARE the new operators.
3. **Column-name independence** — solvers do not hardcode the user's column
   names.  Each solver declares a `SolverContract` listing the column
   *roles* it needs (e.g. NUMERIC_TARGET, DATETIME, BINARY_TARGET).  The
   mapper (rule-based, or pluggable LLM) takes a compact DataFrame profile +
   the contract and returns a `{role -> actual_column_name}` dict.
4. **GT is the test oracle**, not the metric — we compare solver output
   directly against the task's GT csv/json, with task-specific tolerance.
"""
from .contract import Role, RoleSpec, SolverContract
from .profiler import profile_df, profile_to_text
from .mapper import map_columns_rule_based, map_columns_llm
from .comparator import (
    compare_csv_exact,
    compare_csv_numeric_tol,
    compare_json_with_assertions,
)
from .runner import run_task
from .pipeline import Pipeline, PipelineStep, PipelineResult

__all__ = [
    "Role", "RoleSpec", "SolverContract",
    "profile_df", "profile_to_text",
    "map_columns_rule_based", "map_columns_llm",
    "compare_csv_exact", "compare_csv_numeric_tol",
    "compare_json_with_assertions",
    "run_task",
    "Pipeline", "PipelineStep", "PipelineResult",
]
