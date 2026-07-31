"""Typed task-level RADAR operators.

This solver exposes the contract-hardened RADAR task operators to the normal
Operator-Agent registry/catalog path.  It does not read gold labels; it runs a
task-specific typed operator over the input DataFrame and emits a normalized
answer artifact plus diagnostics.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.radar_contract_operators import OPERATORS, run_operator


SUPPORTED_TASK_IDS = tuple(sorted(OPERATORS))


CONTRACT = SolverContract(
    name="radar_typed_task",
    capability="RADAR_typed_contract_task",
    description=(
        "Run a typed, contract-hardened RADAR task operator over the whole "
        "DataFrame. Set static param task_id to one of: "
        + ", ".join(SUPPORTED_TASK_IDS)
        + ". Output is a scalar answer JSON with contract_pass, "
        "contract_score, flags, and repair_actions."
    ),
    roles={
        "task_id": RoleSpec(
            Role.PARAMS,
            "RADAR task id; usually supplied as static param task_id",
            optional=True,
        ),
    },
    static_params={"task_id": ""},
    output_files={
        "answer_json": "radar_typed_answer.json",
        "diagnostics_csv": "radar_typed_diagnostics.csv",
    },
    output_kind={"answer_json": "s", "diagnostics_csv": "s"},
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


class RadarTypedTaskSolver:
    contract = CONTRACT

    def __init__(self, task_id: str | None = None) -> None:
        self.task_id = (task_id or "").strip()

    def run(
        self,
        df: pd.DataFrame,
        mapping: ColumnMapping,
        output_dir: Path,
    ) -> Dict[str, Any]:
        task_id = str(mapping.get("task_id") or self.task_id or "").strip()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        result = run_operator(task_id, df)
        payload = {
            "task_id": task_id,
            "answer": _jsonable(result.answer),
            "contract_pass": bool(result.contract_pass),
            "contract_score": float(result.contract_score),
            "flags": list(result.flags),
            "repair_actions": list(result.repair_actions),
            "supported": task_id in OPERATORS,
            "output_type": "scalar",
        }

        answer_path = output_dir / CONTRACT.output_files["answer_json"]
        answer_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        diagnostics = pd.DataFrame([{
            "task_id": task_id,
            "supported": payload["supported"],
            "contract_pass": payload["contract_pass"],
            "contract_score": payload["contract_score"],
            "answer": payload["answer"],
            "flags": "|".join(payload["flags"]),
            "repair_actions": "|".join(payload["repair_actions"]),
        }])
        diagnostics_path = output_dir / CONTRACT.output_files["diagnostics_csv"]
        diagnostics.to_csv(diagnostics_path, index=False)

        return {
            "answer_json": str(answer_path),
            "diagnostics_csv": str(diagnostics_path),
            "answer_dict": payload,
        }


def get_solver(task_id: str | None = None) -> RadarTypedTaskSolver:
    return RadarTypedTaskSolver(task_id=task_id)


__all__ = [
    "CONTRACT",
    "RadarTypedTaskSolver",
    "SUPPORTED_TASK_IDS",
    "get_solver",
]
