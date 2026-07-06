"""Build lag features for a numeric series (V8 Phase 3 §P0-7).

Adds columns ``<value>_lag_1``, ``<value>_lag_2``, ... to the input
table, optionally grouped by an id column and sorted by a time column.

中文：给指定数值列追加 lag_K 列，可以按个体分组、按时间排序。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError


TIME_SERIES_LAG_CONTRACT = SolverContract(
    name="time_series_lag",
    capability="F08_time_series",
    description=(
        "Append lag features to a numeric column.  Optional id_col "
        "(per-group lag) and time_col (sort before lagging).  "
        "static_params.lags is a list of positive ints (e.g. [1, 2, 3]).  "
        "Output: csv with original columns + <value>_lag_<k> columns."
    ),
    roles={
        "value_col": RoleSpec(Role.NUMERIC, "numeric column to lag"),
        "id_col":    RoleSpec(Role.ID,
                                "subject / entity id for per-group lag",
                                optional=True),
        "time_col":  RoleSpec(Role.DATETIME,
                                "sort key before lagging (datetime or numeric)",
                                optional=True),
    },
    static_params={
        "lags":       [1],
        "fill_value": None,    # None → keep NaN; 0 → fill with 0; 'ffill'
    },
    output_files={"lagged_csv": "time_series_lag.csv"},
    output_kind={"lagged_csv": "t"},
)


class TimeSeriesLagSolver:
    contract = TIME_SERIES_LAG_CONTRACT

    def __init__(self, lags: Sequence[int] = (1,),
                  fill_value: Any = None) -> None:
        try:
            self.lags = sorted({int(k) for k in lags if int(k) > 0})
        except Exception:
            raise OperatorInputError(
                "INVALID_STAT", solver="time_series_lag",
                stat=f"lags={lags!r}",
                whitelist=["lags must be a list of positive integers"],
            )
        if not self.lags:
            self.lags = [1]
        self.fill_value = fill_value

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        v_col = mapping.get("value_col")
        id_col = mapping.get("id_col")
        t_col = mapping.get("time_col")

        if not v_col or v_col not in df.columns:
            raise OperatorInputError(
                "COLUMN_NOT_FOUND", solver="time_series_lag",
                col=v_col, available=list(df.columns)[:20],
            )
        if not pd.api.types.is_numeric_dtype(df[v_col]):
            raise OperatorInputError(
                "COLUMN_NOT_NUMERIC", solver="time_series_lag",
                col=v_col, observed_dtype=str(df[v_col].dtype),
            )
        for nm, col in (("id_col", id_col), ("time_col", t_col)):
            if col is not None and col not in df.columns:
                raise OperatorInputError(
                    "COLUMN_NOT_FOUND", solver="time_series_lag",
                    col=col, available=list(df.columns)[:20],
                )

        work = df.copy()
        # Sort: by id (stable) then by time/index.
        if t_col is not None:
            try:
                work["__t__"] = pd.to_datetime(work[t_col], errors="raise")
            except Exception:
                # numeric time index also fine
                if pd.api.types.is_numeric_dtype(work[t_col]):
                    work["__t__"] = work[t_col]
                else:
                    raise OperatorInputError(
                        "COLUMN_NOT_NUMERIC", solver="time_series_lag",
                        col=t_col,
                        observed_dtype=str(work[t_col].dtype),
                    )
            sort_keys: List[str] = []
            if id_col is not None:
                sort_keys.append(id_col)
            sort_keys.append("__t__")
            work = work.sort_values(sort_keys).reset_index(drop=True)
            work = work.drop(columns=["__t__"])
        elif id_col is not None:
            work = work.sort_values(id_col, kind="stable").reset_index(drop=True)

        for k in self.lags:
            new_col = f"{v_col}_lag_{k}"
            if id_col is not None:
                lagged = work.groupby(id_col, sort=False)[v_col].shift(k)
            else:
                lagged = work[v_col].shift(k)
            if self.fill_value is not None:
                if self.fill_value == "ffill":
                    if id_col is not None:
                        lagged = (work.assign(__v=lagged)
                                       .groupby(id_col, sort=False)["__v"]
                                       .ffill())
                    else:
                        lagged = lagged.ffill()
                else:
                    lagged = lagged.fillna(self.fill_value)
            work[new_col] = lagged

        path = Path(output_dir) / TIME_SERIES_LAG_CONTRACT.output_files["lagged_csv"]
        work.to_csv(path, index=False)
        return {"lagged_csv": str(path),
                "n_lag_columns_added": len(self.lags),
                "lags": self.lags,
                "value_col": v_col,
                "grouped_by": id_col,
                "sorted_by_time": t_col}


def get_solver(lags: Sequence[int] = (1,),
                fill_value: Any = None) -> TimeSeriesLagSolver:
    return TimeSeriesLagSolver(lags=lags, fill_value=fill_value)


def selftest() -> Dict[str, Any]:
    """Two fixtures:
       (a) single series: shift(1), shift(2) must match pandas.Series.shift.
       (b) two-group panel: per-id shift must isolate groups (first row
           of each group is NaN, not the previous group's last value).
    """
    import tempfile
    diffs = []
    # (a) single series
    df_a = pd.DataFrame({"v": [10, 20, 30, 40, 50]})
    with tempfile.TemporaryDirectory() as tmp:
        s = get_solver(lags=[1, 2])
        out = s.run(df=df_a, mapping=ColumnMapping({"value_col": "v"}),
                      output_dir=Path(tmp))
        rd = pd.read_csv(out["lagged_csv"])
        ref1 = df_a["v"].shift(1).tolist()
        ref2 = df_a["v"].shift(2).tolist()

        def _eq(got, ref):
            if len(got) != len(ref):
                return False
            for g, r in zip(got, ref):
                if pd.isna(g) and pd.isna(r):
                    continue
                if pd.isna(g) or pd.isna(r):
                    return False
                if abs(float(g) - float(r)) > 1e-9:
                    return False
            return True

        if not _eq(rd["v_lag_1"].tolist(), ref1):
            diffs.append(f"single-series lag_1 mismatch: "
                         f"got {rd['v_lag_1'].tolist()} vs {ref1}")
        if not _eq(rd["v_lag_2"].tolist(), ref2):
            diffs.append(f"single-series lag_2 mismatch: "
                         f"got {rd['v_lag_2'].tolist()} vs {ref2}")

    # (b) per-group panel
    df_b = pd.DataFrame({
        "pid": ["A", "A", "A", "B", "B"],
        "v":   [1, 2, 3, 100, 200],
    })
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(lags=[1]).run(
            df=df_b, mapping=ColumnMapping({"value_col": "v",
                                              "id_col":   "pid"}),
            output_dir=Path(tmp))
        rd = pd.read_csv(out["lagged_csv"])
        # First row of each group must be NaN
        first_a = rd.loc[rd["pid"] == "A"].iloc[0]["v_lag_1"]
        first_b = rd.loc[rd["pid"] == "B"].iloc[0]["v_lag_1"]
        if not (pd.isna(first_a) and pd.isna(first_b)):
            diffs.append("panel lag should be NaN at group boundary, "
                         f"got A={first_a} B={first_b}")
        # Mid-row of A must equal previous A value, not last-of-B
        mid_a = rd.loc[rd["pid"] == "A"].iloc[1]["v_lag_1"]
        if abs(float(mid_a) - 1.0) > 1e-9:
            diffs.append(f"panel lag A[1] should be 1, got {mid_a}")
    return {"ok": not diffs,
            "summary": ("time_series_lag matches pandas.shift on single "
                          "series & panel" if not diffs
                          else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs}}


__all__ = ["TIME_SERIES_LAG_CONTRACT", "TimeSeriesLagSolver",
            "get_solver", "selftest"]
