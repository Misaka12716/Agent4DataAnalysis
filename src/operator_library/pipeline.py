"""Pipeline composition for Software 1 reference solvers.

Composability contract:

- Every solver writes a *primary csv output* whose first column is an
  identifier (or pass-through key) and whose remaining columns have
  semantically meaningful names.  This csv can be fed straight into the
  next solver as its input DataFrame.
- A solver that needs *additional* metadata from a previous step (e.g.
  a list of "non-normal columns" produced by `normality_test`) accesses
  the previous solver's full output dict via the
  ``previous_outputs`` argument of the step.

Usage::

    from operator_library import (
        Pipeline, PipelineStep,
        solvers,
    )

    pipe = Pipeline([
        PipelineStep(
            name="score_panss",
            solver=solvers.panss_factor_score.get_solver(),
            mapping_override={"id_col": "PatientID",
                              "time_col": "VisitWeek",
                              "positive_items": [...], ...},
        ),
        PipelineStep(
            name="normality",
            solver=solvers.normality_test.get_solver(),
            input_from="score_panss",
            input_output_key="scored_csv",
            mapping_override={
                "test_columns": ["Positive_score", "Negative_score",
                                  "General_score", "Total_score"],
            },
        ),
    ])
    result = pipe.run(initial_input_csv=..., output_dir=...)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .contract import ColumnMapping
from .mapper import map_columns_rule_based
from .profiler import profile_df


@dataclass
class PipelineStep:
    name: str
    solver: Any
    mapping_override: Optional[Dict[str, Any]] = None
    # input_from semantics:
    #   None              → use whichever csv was *last* produced
    #   "__initial__"     → use the pipeline's initial input csv
    #   "<step_name>"     → use the named upstream step's output csv
    input_from: Optional[str] = None
    input_output_key: str = "auto"          # which key in prev outputs is the next input csv


@dataclass
class PipelineResult:
    steps: List[Dict[str, Any]] = field(default_factory=list)
    final_outputs: Dict[str, Any] = field(default_factory=dict)
    ok: bool = True


def _pick_default_csv_key(outputs: Dict[str, Any]) -> Optional[str]:
    for k, v in outputs.items():
        if isinstance(v, str) and v.endswith(".csv"):
            return k
    # fallback: any string-valued key
    for k, v in outputs.items():
        if isinstance(v, (str, Path)):
            return k
    return None


class Pipeline:
    """Sequential composition of solvers."""

    def __init__(self, steps: List[PipelineStep]):
        self.steps = list(steps)

    def run(self, initial_input_csv: str | Path,
            output_dir: str | Path) -> PipelineResult:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        result = PipelineResult()
        outputs_by_step: Dict[str, Dict[str, Any]] = {}
        initial_csv = Path(initial_input_csv)
        last_csv_path: Path = initial_csv

        for step in self.steps:
            # --- resolve input csv path ---
            if step.input_from is None:
                input_csv = last_csv_path
            elif step.input_from == "__initial__":
                input_csv = initial_csv
            else:
                prev = outputs_by_step.get(step.input_from)
                if prev is None:
                    raise KeyError(f"step {step.name!r}: input_from "
                                    f"{step.input_from!r} not yet executed")
                if step.input_output_key == "auto":
                    key = _pick_default_csv_key(prev)
                    if key is None:
                        raise KeyError(f"step {step.name!r}: no csv-shaped "
                                        f"output found in {step.input_from!r}")
                else:
                    key = step.input_output_key
                if key not in prev:
                    raise KeyError(f"step {step.name!r}: key {key!r} "
                                    f"not in outputs of {step.input_from!r} "
                                    f"(have: {list(prev.keys())})")
                input_csv = Path(prev[key])

            # --- load + profile + map ---
            df = pd.read_csv(input_csv)
            profile = profile_df(df)
            if step.mapping_override is not None:
                mapping = ColumnMapping(
                    mapping=dict(step.mapping_override),
                    rationale="pipeline override",
                    source="manual",
                )
            else:
                mapping = map_columns_rule_based(profile, step.solver.contract)

            # --- run ---
            step_dir = output_dir / step.name
            step_dir.mkdir(parents=True, exist_ok=True)
            try:
                outputs = step.solver.run(df=df, mapping=mapping,
                                           output_dir=step_dir)
                step_status = "ok"
                step_error = None
            except BaseException as e:
                outputs = {}
                step_status = "error"
                step_error = f"{type(e).__name__}: {e}"
                result.ok = False

            outputs_by_step[step.name] = outputs

            # advance "last csv" pointer if this step produced one
            if step_status == "ok":
                key = _pick_default_csv_key(outputs)
                if key is not None:
                    last_csv_path = Path(outputs[key])

            result.steps.append({
                "name": step.name,
                "solver": step.solver.contract.name,
                "input_csv": str(input_csv),
                "mapping_source": mapping.source,
                "mapping": mapping.mapping,
                "status": step_status,
                "error": step_error,
                "outputs": {k: (str(v) if isinstance(v, Path) else v)
                            for k, v in outputs.items()
                            if isinstance(v, (str, int, float, dict, list, Path))
                            or v is None},
            })

        result.final_outputs = outputs_by_step
        return result
