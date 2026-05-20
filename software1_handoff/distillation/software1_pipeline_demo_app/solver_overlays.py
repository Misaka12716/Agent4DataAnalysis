"""Per-solver overlays for the pipeline demo.

Some solvers in ``software1_solver`` accept *configuration* through the
constructor (e.g. ``reference_range_flag(reference_ranges)``) rather
than the role-mapping dict.  That makes it impossible for the LLM /
UI to set those fields per-run.

The overlays in this module wrap such solvers so that the relevant
PARAMS appear in the mapping contract too, and we instantiate the
underlying solver with the mapping-supplied value at run time.

Nothing in ``software1_solver`` is modified.

中文说明
========
把「只能在 __init__ 里配」的参数抬到 ``SolverContract.roles`` 的
``Role.PARAMS``，这样规划/映射 JSON 里也能写 ``reference_ranges``。
底层算子实现不动，仅 demo 层包一层。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from distillation.software1_solver.contract import (
    ColumnMapping,
    Role,
    RoleSpec,
    SolverContract,
)
from distillation.software1_solver.solvers import reference_range_flag as _rrf


class ReferenceRangeFlagOverlay:
    """Wraps ``reference_range_flag`` so that ``reference_ranges`` is a
    *role* (PARAMS), enabling LLM/UI configuration per run.
    """

    def __init__(self) -> None:
        base = _rrf.CONTRACT
        new_roles = dict(base.roles)
        new_roles["reference_ranges"] = RoleSpec(
            role=Role.PARAMS,
            description=(
                'reference_ranges: an object {"<lab_col>": '
                '{"low": <number>, "high": <number>}, ...} listing the '
                "lab columns to flag and their adult reference intervals."
            ),
            optional=False,
        )
        self.contract = replace(base, roles=new_roles,
                                 static_params={"reference_ranges": {}})

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        refs = mapping.get("reference_ranges") or {}
        if not isinstance(refs, dict):
            refs = {}
        # build the underlying solver with the supplied ranges and
        # delegate; the underlying solver only requires id_col +
        # lab_columns from the mapping.
        inner = _rrf.get_solver(refs)
        return inner.run(df=df, mapping=mapping, output_dir=output_dir)


def get_reference_range_flag_overlay():
    return ReferenceRangeFlagOverlay()
