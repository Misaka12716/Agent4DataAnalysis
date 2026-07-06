"""Group-wise scalar statistic (V8 Phase 3 §P0-1).

Drop-in for "compute STAT of VALUE_COL split by GROUP_COL".  Shares
the per-group statistic dispatch with ``column_stat`` so a planner
can swap one for the other without re-learning a stat vocabulary.

Output schema is fixed: ``[group, stat, value, n_used, n_total]``.

中文：column_stat 的"按组版本"。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError
from ._numeric_utils import coerce_to_numeric
from .column_stat import (
    _FIXED_STATS, _Q_PATTERN, _WHITELIST_PREVIEW,
    _validate_stat_token, _quantile, _mode, _proportion_in_range,
    _top_k_value,
)


GROUPBY_STAT_CONTRACT = SolverContract(
    name="groupby_stat",
    capability="F02_descriptive_stats_distribution",
    description=(
        "Compute ONE scalar statistic of a numeric column split by the "
        "unique values of a grouping column.  Stat whitelist matches "
        "column_stat (mean/median/sum/count/std/var/min/max/mode/q{N}/"
        "proportion_in_range/top_k_value).  Optional weight_col and "
        "subset_query.  Output: csv [group, stat, value, n_used, n_total]."
    ),
    roles={
        "group_col": RoleSpec(Role.CATEGORICAL,
                                "column whose unique values define groups"),
        "value_col": RoleSpec(Role.NUMERIC,
                                "numeric column to summarise per group"),
        "weight_col": RoleSpec(Role.NUMERIC,
                                 "optional frequency weights",
                                 optional=True),
    },
    static_params={
        "stat": "mean",
        "subset_query": None,
        "value_min": None,
        "value_max": None,
        "k": None,
    },
    output_files={"groupby_csv": "groupby_stat.csv"},
    output_kind={"groupby_csv": "s"},
)


class GroupbyStatSolver:
    contract = GROUPBY_STAT_CONTRACT

    def __init__(self, stat: str = "mean",
                  subset_query: Optional[str] = None,
                  value_min: Optional[float] = None,
                  value_max: Optional[float] = None,
                  k: Optional[int] = None) -> None:
        self.stat = stat
        self.subset_query = subset_query
        self.value_min = value_min
        self.value_max = value_max
        self.k = k

    def _compute_one(self, v: np.ndarray,
                      w: Optional[np.ndarray], stat: str) -> float:
        from .column_stat import _parse_quantile
        q = _parse_quantile(stat)
        if q is not None:
            return _quantile(v, w, q)
        if stat == "median":
            return _quantile(v, w, 0.5)
        if len(v) == 0 and stat != "count":
            return float("nan")
        if stat == "mean":
            if w is None:
                return float(np.mean(v))
            tw = float(w.sum())
            return float(np.sum(v * w) / tw) if tw > 0 else float("nan")
        if stat == "sum":
            return float(np.sum(v * w) if w is not None else np.sum(v))
        if stat == "count":
            return float(np.sum(w) if w is not None else len(v))
        if stat in {"std", "var"}:
            mean = (float(np.average(v, weights=w))
                    if w is not None else float(np.mean(v)))
            if w is None:
                if len(v) <= 1:
                    return 0.0
                var = float(np.sum((v - mean) ** 2) / (len(v) - 1))
            else:
                tw = float(w.sum())
                if tw <= 0:
                    return float("nan")
                var = float(np.sum(w * (v - mean) ** 2) / max(tw - 1, 1.0))
            return var if stat == "var" else float(np.sqrt(var))
        if stat == "min":
            return float(np.min(v))
        if stat == "max":
            return float(np.max(v))
        if stat == "mode":
            return _mode(v, w)
        if stat == "proportion_in_range":
            return _proportion_in_range(v, w, self.value_min, self.value_max,
                                          solver_name="groupby_stat")
        if stat == "top_k_value":
            return _top_k_value(v, self.k, solver_name="groupby_stat")
        raise OperatorInputError("INVALID_STAT", solver="groupby_stat",
                                   stat=stat, whitelist=_WHITELIST_PREVIEW)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        group_col = mapping.get("group_col")
        value_col = mapping.get("value_col")
        weight_col = mapping.get("weight_col")
        # Mirror column_stat: also accept ``stat`` if planner mis-put
        # it into the role-mapping instead of static_params.
        mapping_stat = mapping.get("stat") if hasattr(mapping, "get") else None
        if isinstance(mapping_stat, str) and mapping_stat.strip():
            stat = mapping_stat.strip().lower()
        else:
            stat = (self.stat or "mean").strip().lower()

        for name, col in (("group_col", group_col),
                            ("value_col", value_col)):
            if col is None or col not in df.columns:
                raise OperatorInputError(
                    "COLUMN_NOT_FOUND", solver="groupby_stat",
                    col=col, available=list(df.columns)[:20],
                )
        if not pd.api.types.is_numeric_dtype(df[value_col]):
            coerced, ok, rate = coerce_to_numeric(df[value_col])
            if ok:
                df = df.copy()
                df[value_col] = coerced
            else:
                raise OperatorInputError(
                    "COLUMN_NOT_COERCIBLE", solver="groupby_stat",
                    col=value_col, dtype=str(df[value_col].dtype),
                    coerce_rate=f"{rate:.0%}",
                )
        if weight_col is not None:
            if (weight_col not in df.columns
                    or not pd.api.types.is_numeric_dtype(df[weight_col])):
                wc_ok = False
                if weight_col in df.columns:
                    wc_coerced, wc_ok, _ = coerce_to_numeric(df[weight_col])
                    if wc_ok:
                        df = df.copy()
                        df[weight_col] = wc_coerced
                if not wc_ok:
                    raise OperatorInputError(
                        "WEIGHT_COL_INVALID", solver="groupby_stat",
                        col=weight_col,
                        observed_dtype=(str(df[weight_col].dtype)
                                          if weight_col in df.columns
                                          else "absent"),
                    )
        if not _validate_stat_token(stat):
            raise OperatorInputError(
                "INVALID_STAT", solver="groupby_stat",
                stat=stat, whitelist=_WHITELIST_PREVIEW,
            )

        n_total = int(len(df))
        work = df
        if self.subset_query:
            try:
                work = df.query(self.subset_query)
            except Exception as e:
                raise OperatorInputError(
                    "SUBSET_QUERY_INVALID", solver="groupby_stat",
                    query=self.subset_query,
                    reason=f"{type(e).__name__}: {e}",
                )

        rows: List[Dict[str, Any]] = []
        # 用 fillna placeholder 保留 NaN 组（否则 pandas.groupby 默认丢 NaN
        # → 拿不到"NaN 组的统计"，对临床数据常常是错的）
        group_keys = work[group_col].astype(object).where(
            work[group_col].notna(), "<NaN>")
        for gname, sub in work.groupby(group_keys, sort=True, dropna=False):
            v = sub[value_col]
            if weight_col is not None:
                w = sub[weight_col]
                mask = v.notna() & w.notna()
                v_arr = v[mask].astype(float).values
                w_arr = w[mask].astype(float).values
            else:
                v_arr = v.dropna().astype(float).values
                w_arr = None
            n_used = int(len(v_arr))
            try:
                value = self._compute_one(v_arr, w_arr, stat)
            except OperatorInputError:
                raise
            rows.append({
                "group":   str(gname),
                "stat":    stat,
                "value":   value,
                "n_used":  n_used,
                "n_total": int(len(sub)),
            })

        out = pd.DataFrame(rows)
        path = Path(output_dir) / GROUPBY_STAT_CONTRACT.output_files["groupby_csv"]
        out.to_csv(path, index=False)
        return {
            "groupby_csv": str(path),
            "n_groups": len(rows),
            "n_total": n_total,
            "stat": stat,
            "weighted": weight_col is not None,
        }


def get_solver(stat: str = "mean",
                subset_query: Optional[str] = None,
                value_min: Optional[float] = None,
                value_max: Optional[float] = None,
                k: Optional[int] = None) -> GroupbyStatSolver:
    return GroupbyStatSolver(stat=stat, subset_query=subset_query,
                                value_min=value_min, value_max=value_max,
                                k=k)


def selftest() -> Dict[str, Any]:
    """Fixture: 3 groups × balanced sizes, compare against numpy / pandas
    ground truth for mean/median/q75/std/count.

    中文：3 个分组的固定 fixture，对 numpy/pandas 独立算结果逐组对账。
    """
    import tempfile
    rng = np.random.default_rng(2026)
    df = pd.DataFrame({
        "g": (["A"] * 40 + ["B"] * 30 + ["C"] * 30),
        "x": np.concatenate([
            rng.normal(10, 1, 40),
            rng.normal(20, 2, 30),
            rng.normal(5, 0.5, 30),
        ]),
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for stat in ("mean", "median", "q75", "std", "count"):
            s = get_solver(stat=stat)
            out = s.run(df=df, mapping=ColumnMapping(
                {"group_col": "g", "value_col": "x"}),
                         output_dir=Path(tmp))
            res = pd.read_csv(out["groupby_csv"]).set_index("group")
            for g in ("A", "B", "C"):
                ref_vals = df.loc[df["g"] == g, "x"].astype(float).values
                if stat == "mean":
                    ref = float(np.mean(ref_vals))
                elif stat == "median":
                    ref = float(np.median(ref_vals))
                elif stat == "q75":
                    ref = float(np.quantile(ref_vals, 0.75))
                elif stat == "std":
                    ref = float(np.std(ref_vals, ddof=1))
                else:  # count
                    ref = float(len(ref_vals))
                got = float(res.loc[g, "value"])
                if abs(got - ref) > 1e-9:
                    diffs.append(f"{stat} group={g}: got {got} vs ref {ref}")
    return {"ok": not diffs,
            "summary": ("groupby_stat matches numpy/pandas ground truth"
                          if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs}}


__all__ = ["GROUPBY_STAT_CONTRACT", "GroupbyStatSolver",
            "get_solver", "selftest"]
