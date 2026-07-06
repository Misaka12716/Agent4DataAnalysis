"""Single-column single-statistic solver  (V8 Phase 2 §4).

Compute ONE scalar statistic of ONE numeric column, optionally on a
row subset (``subset_query``), optionally with frequency weights
(``weight_col``).  Output is always a one-row csv with stable schema
``[column, stat, value, n_total, n_used]`` so downstream code never
has to branch on which stat was asked for.

Why this exists
---------------
A large fraction of QRData / RADAR / RDAB final-extraction steps boil
down to "read csv → filter rows → take column → compute one number".
Today they all fall through to ``__coder__`` because no single-stat
operator exists.  This file is that operator.

It is deliberately a *thin* solver:
  - one numeric column (mandatory)
  - one optional weight column (frequency tables)
  - one optional pandas-query subset (rows)
  - one stat token from a hard-coded whitelist
That keeps the surface tiny and reproducible — for anything more
exotic the planner is still expected to use ``__coder__``.

中文说明
========
"单列单值统计"原子算子。覆盖 mean/median/分位数/range 比例/top-k 等
60–70% 的 Coder 兜底场景，输出一行 csv 五列固定 schema。
**只对一列做事**，泛化能力靠 ``subset_query`` 和 ``weight_col``。
比 describe_full 更窄、更可控；planner 选它时不需要写代码。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError
from ._numeric_utils import coerce_to_numeric


# ---------------------------------------------------------------------------
# Stat whitelist
# ---------------------------------------------------------------------------
# Tokens accepted verbatim:
_FIXED_STATS = frozenset({
    "mean", "median", "sum", "count", "std", "var",
    "min", "max", "mode",
    "proportion_in_range",
    "top_k_value",
})

# Quantile pattern: q1, q05, q50, q975, ...   1..99 / 0.01..99.99 OK.
_Q_PATTERN = re.compile(r"^q(\d{1,4})$")

_WHITELIST_PREVIEW = sorted(_FIXED_STATS | {"q{1..99}"})


def _validate_stat_token(stat: str) -> bool:
    if not isinstance(stat, str):
        return False
    s = stat.strip().lower()
    if s in _FIXED_STATS:
        return True
    return bool(_Q_PATTERN.match(s))


def _parse_quantile(stat: str) -> Optional[float]:
    """Return quantile in [0, 1] or None if `stat` is not q-form.

    ``q5`` → 0.05, ``q50`` → 0.5, ``q975`` → 0.975.  We treat the
    trailing digits as "percent" with implicit decimals so that both
    ``q5`` and ``q05`` mean the 5th percentile; ``q975`` means 97.5%.
    """
    m = _Q_PATTERN.match(stat.strip().lower())
    if not m:
        return None
    digits = m.group(1)
    # 1-2 digit → percent整数；3-4 位 → 带小数（最后一位是 0.1%）
    if len(digits) <= 2:
        pct = float(digits)
    else:
        pct = float(digits) / (10 ** (len(digits) - 2))
    if not (0.0 <= pct <= 100.0):
        return None
    return pct / 100.0


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------
COLUMN_STAT_CONTRACT = SolverContract(
    name="column_stat",
    capability="F02_descriptive_stats_distribution",
    description=(
        "Compute one scalar statistic of one numeric column.  Stat "
        "whitelist: mean / median / sum / count / std / var / min / "
        "max / mode / q{N} (1..99 or decimal e.g. q975) / "
        "proportion_in_range (needs value_min and/or value_max) / "
        "top_k_value (needs k>=1).  Optional subset_query (pandas query) "
        "and weight_col (frequency weights).  Output: single-row csv "
        "[column, stat, value, n_total, n_used]."
    ),
    roles={
        "column": RoleSpec(
            Role.NUMERIC,
            "the numeric column to summarise",
        ),
        "weight_col": RoleSpec(
            Role.NUMERIC,
            "frequency weights for (value, count)-shaped frequency tables; "
            "if supplied, mean / median / variance / quantiles / sum / "
            "proportion_in_range are computed weighted by this column",
            optional=True,
        ),
    },
    static_params={
        # whitelisted stat token; see _FIXED_STATS / _Q_PATTERN above.
        # Default is ``mean`` because >60% of "summary of one column"
        # tasks ask for mean; median is the special case.  Planner is
        # expected to set this explicitly via static_params, but if it
        # accidentally puts ``stat`` in the role-mapping (a common
        # confusion) we also accept that — see run() below.
        "stat": "mean",
        # optional pandas-query string applied to the input dataframe
        # BEFORE any computation; e.g. "age >= 18 and `OECD member` == 1"
        "subset_query": None,
        # for proportion_in_range: [value_min, value_max]; either bound
        # can be None for one-sided intervals
        "value_min": None,
        "value_max": None,
        # for top_k_value: 1=largest, 2=second-largest, ...
        "k": None,
    },
    output_files={"stat_csv": "column_stat.csv"},
    output_kind={"stat_csv": "s"},
)


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------
class ColumnStatSolver:
    contract = COLUMN_STAT_CONTRACT

    def __init__(
        self,
        stat: str = "mean",
        subset_query: Optional[str] = None,
        value_min: Optional[float] = None,
        value_max: Optional[float] = None,
        k: Optional[int] = None,
    ) -> None:
        self.stat = stat
        self.subset_query = subset_query
        self.value_min = value_min
        self.value_max = value_max
        self.k = k

    # -- main entry ------------------------------------------------------
    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        col = mapping.get("column")
        weight_col = mapping.get("weight_col")
        # Accept ``stat`` from EITHER static_params (preferred, set in
        # __init__) OR the role-mapping (planner sometimes mis-puts it
        # there).  Mapping wins if both are set because a fresh per-call
        # value is more specific than a constructor default.
        mapping_stat = mapping.get("stat") if hasattr(mapping, "get") else None
        if isinstance(mapping_stat, str) and mapping_stat.strip():
            stat_raw = mapping_stat
        else:
            stat_raw = self.stat or "mean"
        stat = stat_raw.strip().lower()

        # ---- 1. input validation (all paths raise OperatorInputError) --
        if col is None or col not in df.columns:
            raise OperatorInputError(
                "COLUMN_NOT_FOUND",
                solver="column_stat",
                col=col,
                available=list(df.columns)[:20],
            )
        if not pd.api.types.is_numeric_dtype(df[col]):
            # Try the auto-coerce path (commas / % / $ / whitespace
            # stripping).  If ≥70% of cells coerce, we silently use the
            # cleaned series — otherwise raise COLUMN_NOT_COERCIBLE so
            # the planner / coder gets a clearer signal than the legacy
            # COLUMN_NOT_NUMERIC.
            coerced, ok, rate = coerce_to_numeric(df[col])
            if ok:
                df = df.copy()
                df[col] = coerced
            else:
                raise OperatorInputError(
                    "COLUMN_NOT_COERCIBLE",
                    solver="column_stat",
                    col=col,
                    dtype=str(df[col].dtype),
                    coerce_rate=f"{rate:.0%}",
                )
        if weight_col is not None:
            if (weight_col not in df.columns
                    or not pd.api.types.is_numeric_dtype(df[weight_col])):
                # Same coerce-then-validate path for the weights column.
                if weight_col in df.columns:
                    wc_coerced, wc_ok, _ = coerce_to_numeric(df[weight_col])
                else:
                    wc_ok = False
                if wc_ok:
                    if df is not None:
                        df = df.copy()
                        df[weight_col] = wc_coerced
                else:
                    raise OperatorInputError(
                        "WEIGHT_COL_INVALID",
                        solver="column_stat",
                        col=weight_col,
                        observed_dtype=(
                            str(df[weight_col].dtype)
                            if weight_col in df.columns else "absent"
                        ),
                    )
        if not _validate_stat_token(stat):
            raise OperatorInputError(
                "INVALID_STAT",
                solver="column_stat",
                stat=stat,
                whitelist=_WHITELIST_PREVIEW,
            )

        # ---- 2. subset --------------------------------------------------
        n_total = int(len(df))
        work = df
        if self.subset_query:
            try:
                work = df.query(self.subset_query)
            except Exception as e:
                raise OperatorInputError(
                    "SUBSET_QUERY_INVALID",
                    solver="column_stat",
                    query=self.subset_query,
                    reason=f"{type(e).__name__}: {e}",
                )

        # ---- 3. prepare (value, weight) drop-NaN-pairwise --------------
        v = work[col]
        if weight_col is not None:
            w = work[weight_col]
            mask = v.notna() & w.notna()
            v = v[mask].astype(float).values
            w = w[mask].astype(float).values
        else:
            v = v.dropna().astype(float).values
            w = None
        n_used = int(len(v))

        # ---- 4. dispatch ------------------------------------------------
        if n_used == 0 and stat not in {"count"}:
            value: Any = float("nan")
        else:
            value = self._compute(v, w, stat)

        # ---- 5. write output -------------------------------------------
        out = pd.DataFrame([{
            "column":   str(col),
            "stat":     stat,
            "value":    value,
            "n_total":  n_total,
            "n_used":   n_used,
        }])
        path = Path(output_dir) / COLUMN_STAT_CONTRACT.output_files["stat_csv"]
        out.to_csv(path, index=False)
        return {
            "stat_csv": str(path),
            "value":    value,
            "n_total":  n_total,
            "n_used":   n_used,
            "weighted": weight_col is not None,
        }

    # -- helpers ---------------------------------------------------------
    def _compute(self, v: np.ndarray, w: Optional[np.ndarray],
                 stat: str) -> float:
        """Dispatch to the requested statistic.  v/w are aligned 1-D."""
        # Quantile family (handles q-pattern + median synonym)
        q = _parse_quantile(stat)
        if q is not None:
            return _quantile(v, w, q)
        if stat == "median":
            return _quantile(v, w, 0.5)

        # Unweighted-by-default stats also accept weights
        if stat == "mean":
            if w is None:
                return float(np.mean(v))
            tw = float(w.sum())
            return float(np.sum(v * w) / tw) if tw > 0 else float("nan")
        if stat == "sum":
            if w is None:
                return float(np.sum(v))
            # weighted sum: Σ v_i · w_i  (treats w as "how many times v_i appears")
            return float(np.sum(v * w))
        if stat == "count":
            if w is None:
                return float(len(v))
            return float(np.sum(w))
        if stat in {"std", "var"}:
            mean = (float(np.average(v, weights=w))
                    if w is not None else float(np.mean(v)))
            if w is None:
                # sample variance, ddof=1
                if len(v) <= 1:
                    return 0.0
                var = float(np.sum((v - mean) ** 2) / (len(v) - 1))
            else:
                tw = float(w.sum())
                if tw <= 0:
                    return float("nan")
                # weighted variance (frequency interpretation, ddof≈1)
                neff = tw
                var = float(np.sum(w * (v - mean) ** 2) / max(neff - 1, 1.0))
            return var if stat == "var" else float(np.sqrt(var))
        if stat == "min":
            return float(np.min(v))
        if stat == "max":
            return float(np.max(v))
        if stat == "mode":
            return _mode(v, w)
        if stat == "proportion_in_range":
            return _proportion_in_range(v, w, self.value_min, self.value_max,
                                         solver_name="column_stat")
        if stat == "top_k_value":
            return _top_k_value(v, self.k, solver_name="column_stat")

        # Should be unreachable thanks to _validate_stat_token above.
        raise OperatorInputError(
            "INVALID_STAT",
            solver="column_stat",
            stat=stat,
            whitelist=_WHITELIST_PREVIEW,
        )


# ---------------------------------------------------------------------------
# Stat helpers (module-level for clarity / testability)
# ---------------------------------------------------------------------------
def _quantile(v: np.ndarray, w: Optional[np.ndarray], q: float) -> float:
    """q in [0, 1].  Unweighted = np.quantile(linear).  Weighted = inverse
    CDF on the sorted (value, weight) pairs (a.k.a. type-1 / step CDF)."""
    if len(v) == 0:
        return float("nan")
    if w is None:
        return float(np.quantile(v, q))
    order = np.argsort(v)
    vs = v[order]
    ws = w[order]
    cw = np.cumsum(ws)
    total = float(cw[-1])
    if total <= 0:
        return float("nan")
    target = q * total
    idx = int(np.searchsorted(cw, target, side="left"))
    if idx >= len(vs):
        idx = len(vs) - 1
    return float(vs[idx])


def _mode(v: np.ndarray, w: Optional[np.ndarray]) -> float:
    """Most frequent value; ties → smallest value (deterministic).
    Weighted variant sums weights per unique value."""
    if len(v) == 0:
        return float("nan")
    uniq, inv = np.unique(v, return_inverse=True)
    if w is None:
        counts = np.bincount(inv)
    else:
        counts = np.bincount(inv, weights=w)
    best = np.where(counts == counts.max())[0]
    return float(uniq[best[0]])


def _proportion_in_range(v: np.ndarray, w: Optional[np.ndarray],
                          lo: Optional[float], hi: Optional[float],
                          solver_name: str) -> float:
    """Inclusive on both ends.  At least one of lo/hi must be supplied."""
    if lo is None and hi is None:
        raise OperatorInputError(
            "MISSING_STAT_PARAM",
            solver=solver_name,
            stat="proportion_in_range",
            param="value_min OR value_max",
        )
    mask = np.ones_like(v, dtype=bool)
    if lo is not None:
        mask &= (v >= float(lo))
    if hi is not None:
        mask &= (v <= float(hi))
    if w is None:
        return float(np.mean(mask))
    tw = float(w.sum())
    if tw <= 0:
        return float("nan")
    return float(np.sum(w[mask]) / tw)


def _top_k_value(v: np.ndarray, k: Optional[int],
                  solver_name: str) -> float:
    """k=1 → largest, k=2 → second-largest, ...  Ties not collapsed."""
    if k is None or not isinstance(k, (int, np.integer)) or k < 1:
        raise OperatorInputError(
            "MISSING_STAT_PARAM",
            solver=solver_name,
            stat="top_k_value",
            param="k (positive integer)",
        )
    if len(v) == 0:
        return float("nan")
    k = int(k)
    if k > len(v):
        return float("nan")
    # 倒数第 k 大 = 降序排序后 index k-1
    return float(np.sort(v)[-k])


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def get_solver(
    stat: str = "mean",
    subset_query: Optional[str] = None,
    value_min: Optional[float] = None,
    value_max: Optional[float] = None,
    k: Optional[int] = None,
) -> ColumnStatSolver:
    return ColumnStatSolver(
        stat=stat,
        subset_query=subset_query,
        value_min=value_min,
        value_max=value_max,
        k=k,
    )


__all__ = [
    "COLUMN_STAT_CONTRACT",
    "ColumnStatSolver",
    "get_solver",
]
