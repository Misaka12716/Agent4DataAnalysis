"""Descriptive statistics + distribution solvers.

Covers Q07 (描述性统计) and Q34 (数据分布):

  - describe_full        : per-column count/mean/std/min/Q1/median/Q3/max
                           + skew/kurtosis/IQR/MAD (more complete than
                           pandas.describe()).
  - distribution_histogram: per-column histogram (n_bins) → long csv.

中文说明
========
描述性统计 + 分布直方图。比 ``pandas.describe()`` 更全。

1. ``describe_full``：逐列输出 12 个统计量
   - count, mean, std (ddof=1), min, q25, median, q75, max
   - iqr (q75-q25), mad (median absolute deviation, 稳健尺度)
   - skewness, kurtosis（来自 ``scipy.stats``，default ``fisher=True``、
     ``bias=True``）
   - 输入：可选 ``numeric_columns``（不传就自动选所有数值列）
   - 输出 ``stats_csv`` = ``describe_full.csv``，一行一列

2. ``distribution_histogram``：等宽直方图（``n_bins`` 默认 20）
   - 输入：可选 ``numeric_columns``
   - 输出 ``hist_csv`` = ``distribution_histogram.csv`` 长表
     [column, bin_index, bin_left, bin_right, count, density]
   - density = count / total_count（不是真概率密度，是相对频率）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError


# ---------------------------------------------------------------------------
# Shape sniff helpers (V8 Phase 2 §3)
# ---------------------------------------------------------------------------
# Stat-name tokens that, if found as column names in the *input* df,
# suggest the caller mis-routed describe_full's own output (a summary
# table) back into a stats solver.  We require ≥4 hits to fire so that
# real-world clinical tables that happen to have one "min" / "max"
# column don't trip the sniff.
_SUMMARY_STAT_NAMES = frozenset({
    "count", "mean", "std", "min", "max",
    "q25", "q50", "q75", "q05", "q95",
    "25%", "50%", "75%",
    "median", "iqr", "mad",
    "skewness", "kurtosis",
    "variance", "var",
    "n_missing", "missing_rate", "n_unique",
})

# Column-name tokens suggesting a "count / weight" column for a
# frequency table.  Kept narrow to reduce false positives.
_WEIGHT_NAME_HINTS = (
    "count", "freq", "frequency",
    "num_of_", "n_of_", "n_observations",
    "weight", "weights",
    "occurrences",
)


def _looks_like_summary_table(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """Detect if df looks like the output of describe_full / missing_summary
    rather than row-level data.

    Returns (is_summary, hit_columns).  Conservative: requires ≥4 stat-name
    column hits before firing, so a clinical table with a single ``min``
    column does NOT trip this.
    """
    hits = [str(c) for c in df.columns
            if str(c).strip().lower() in _SUMMARY_STAT_NAMES]
    return (len(hits) >= 4, hits)


def _looks_like_frequency_table(
    df: pd.DataFrame,
    numeric_cols: List[str],
) -> Optional[str]:
    """If df looks like a (value, count) frequency table, return the
    detected weight column name; else None.

    Heuristic (all must hold):
      1. exactly 1 or 2 numeric columns
      2. one of them is named like a count/weight column (see hints)
         AND contains non-negative integers
      3. the other numeric column has higher cardinality (i.e. it is
         the "value" column)

    We do NOT auto-rewrite anything; downstream callers decide whether
    to use the hint (warning vs. fail-fast).
    """
    if len(numeric_cols) not in (1, 2):
        return None
    cand_weights = []
    for c in numeric_cols:
        name = str(c).lower()
        if any(h in name for h in _WEIGHT_NAME_HINTS):
            col = df[c].dropna()
            if len(col) == 0:
                continue
            # 非负整数（允许 float 但全是整数值）
            if (col >= 0).all() and np.allclose(col, col.astype(int)):
                cand_weights.append(c)
    if not cand_weights:
        return None
    if len(numeric_cols) == 1:
        # 1 numeric column that itself looks like a weight column is
        # ambiguous — could be a real count series.  Don't fire.
        return None
    # 2 numeric columns + exactly one looks like a weight → frequency.
    weight = cand_weights[0]
    other = [c for c in numeric_cols if c != weight][0]
    if df[other].nunique(dropna=True) < df[weight].nunique(dropna=True):
        # 反过来：weight 列基数比 value 列还高 → 不是频数表
        return None
    return weight


# ---------------------------------------------------------------------------
# describe_full
# ---------------------------------------------------------------------------
# Contract 说明：
#   - numeric_columns optional：不传就 fallback 到所有 is_numeric_dtype 列
#   - 没有 static_params：统计量集合固定（ddof=1 / Fisher kurtosis）
DESCRIBE_CONTRACT = SolverContract(
    name="describe_full",
    capability="F02_descriptive_stats_distribution",
    description=(
        "Per-column descriptive statistics: count, mean, std, min, "
        "Q1 (25%), median (50%), Q3 (75%), max, skewness, kurtosis, "
        "IQR, MAD (median absolute deviation).  Output: csv with one "
        "row per numeric column."),
    roles={
        "numeric_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "numeric columns to summarise (default: all numeric)",
            optional=True,
        ),
    },
    output_files={"stats_csv": "describe_full.csv"},
    output_kind={"stats_csv": "s"},
)


class DescribeFullSolver:
    contract = DESCRIBE_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        # ---- V8 Phase-2 input sniff ----------------------------------
        # (a) Summary-table mis-routing: fail-fast.  Computing
        #     "statistics of statistics" is essentially always wrong;
        #     better to surface a clear error than to silently produce
        #     nonsense.
        is_summary, summary_hits = _looks_like_summary_table(df)
        if is_summary:
            raise OperatorInputError(
                "INPUT_IS_SUMMARY_TABLE",
                solver="describe_full",
                stat_cols=summary_hits[:8],
            )

        # (b) Build the column list.  Non-numeric requested columns are
        #     skipped (legacy behaviour was to crash later in
        #     ``astype(float)``).  This makes the solver robust to a
        #     planner that handed us a mixed list.
        requested = mapping.get("numeric_columns")
        skipped: List[str] = []
        if requested:
            cols: List[str] = []
            for c in requested:
                if c in df.columns and pd.api.types.is_numeric_dtype(df[c]):
                    cols.append(c)
                else:
                    skipped.append(c)
        else:
            cols = [c for c in df.columns
                    if pd.api.types.is_numeric_dtype(df[c])
                    and c != "__row_id__"]
        if not cols:
            raise OperatorInputError(
                "NO_NUMERIC_COLUMNS",
                solver="describe_full",
            )

        # (c) Frequency-table warning (do NOT fail; describe_full per-column
        #     stats are still well-defined on a freq table, they just
        #     answer a different question than the user probably wants).
        warnings: List[str] = []
        freq_weight = _looks_like_frequency_table(df, cols)
        if freq_weight is not None:
            value_cols = [c for c in cols if c != freq_weight]
            warnings.append(
                f"input looks like a frequency table with weight column "
                f"{freq_weight!r}; describe_full computes per-column stats "
                f"NOT weighted by {freq_weight!r}.  If you wanted "
                f"frequency-weighted statistics, use column_stat with "
                f"weight_col={freq_weight!r} on value column(s) "
                f"{value_cols}."
            )

        rows = []
        for c in cols:
            # 整列 dropna 再 astype(float)：只用非缺失值算所有统计量；
            # 全空列也允许（rows 里只塞 column 名占位）
            s = df[c].dropna().astype(float)
            if len(s) == 0:
                rows.append({"column": c})
                continue
            # 用 numpy.percentile（线性插值）一次拿三个分位数；与
            # pandas.quantile() 默认行为一致，可直接对账
            q1, med, q3 = np.percentile(s, [25, 50, 75])
            rows.append({
                "column":    c,
                "count":     int(len(s)),
                "mean":      float(s.mean()),
                # ddof=1 = 样本标准差（unbiased）；len<=1 时定义 0 防止 NaN
                "std":       float(s.std(ddof=1)) if len(s) > 1 else 0.0,
                "min":       float(s.min()),
                "q25":       float(q1),
                "median":    float(med),
                "q75":       float(q3),
                "max":       float(s.max()),
                "iqr":       float(q3 - q1),
                # MAD = median(|x - median|)，比 std 更抗极端值
                "mad":       float((s - med).abs().median()),
                # scipy.stats.skew/kurtosis 默认 bias=True、Fisher 定义
                # （正态 → 0），与 R 的 e1071::skewness type=1 一致
                "skewness":  float(sps.skew(s.values)),
                "kurtosis":  float(sps.kurtosis(s.values)),
            })
        out = pd.DataFrame(rows)
        path = Path(output_dir) / DESCRIBE_CONTRACT.output_files["stats_csv"]
        out.to_csv(path, index=False)
        result: Dict[str, Any] = {"stats_csv": str(path),
                                    "stats_df": out,
                                    "n_columns": len(cols)}
        if skipped:
            result["skipped_columns"] = skipped
        if warnings:
            result["warnings"] = warnings
        return result


# ---------------------------------------------------------------------------
# distribution_histogram
# ---------------------------------------------------------------------------
# Contract 说明：
#   - numeric_columns optional：不传就所有数值列
#   - weight_col optional (V8 Phase 2 §3.3)：若提供，histogram 的 count
#     用 ``sum(weight)`` 而非 ``len()`` —— 频数表 (value, count) 直接画
#     正确的分布
#   - static_params.n_bins 默认 20（经验值；20 个 bin 在常规连续变量
#     上既能看出形状又不会过拟合到锯齿）
#   - static_params.bin_range optional [low, high]（V8 Phase 2 §3.3）：
#     指定时只在 [low, high] 范围内等宽分箱；用于"180–185cm 区间比例"
#     这类问题（配合 n_bins=1 即可一发拿到答案）
HIST_CONTRACT = SolverContract(
    name="distribution_histogram",
    capability="F02_descriptive_stats_distribution",
    description=(
        "Per-column equal-width histogram with `n_bins` bins.  "
        "Optional `weight_col` for (value, count)-shaped frequency "
        "tables (counts are summed weights).  Optional `bin_range` "
        "[low, high] to restrict the histogram domain — combine with "
        "n_bins=1 to read off the proportion in an interval.  "
        "Output: long-format csv [column, bin_left, bin_right, count, "
        "density]."),
    roles={
        "numeric_columns": RoleSpec(Role.NUMERIC_LIST,
                                     "numeric columns to histogram",
                                     optional=True),
        "weight_col": RoleSpec(
            Role.NUMERIC,
            "frequency weights for (value, count)-shaped tables; if "
            "supplied, bin counts are sum-of-weights rather than row "
            "counts",
            optional=True,
        ),
    },
    static_params={
        "n_bins": 20,
        # [low, high] domain restriction; None ⇒ use observed min/max
        # of each column independently (legacy behaviour).
        "bin_range": None,
    },
    output_files={"hist_csv": "distribution_histogram.csv"},
    output_kind={"hist_csv": "s"},
)


class DistributionHistogramSolver:
    contract = HIST_CONTRACT

    def __init__(self, n_bins: int = 20,
                  bin_range: Optional[Tuple[float, float]] = None):
        """中文：

        :param n_bins:    等宽直方图 bin 数。默认 20；小样本 (<50) 用 10，
                          大样本 (>10000) 可以加到 50。``n_bins=1`` 配合
                          ``bin_range=[lo, hi]`` 可以读出 [lo, hi] 区间
                          内的样本比例（看 density 列）。
        :param bin_range: 指定 [low, high] 时只对该范围分箱；None 表示
                          按各列实际 min/max 分。
        """
        self.n_bins = int(n_bins)
        self.bin_range = bin_range

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        # ---- V8 Phase-2 input sniff ----------------------------------
        is_summary, summary_hits = _looks_like_summary_table(df)
        if is_summary:
            raise OperatorInputError(
                "INPUT_IS_SUMMARY_TABLE",
                solver="distribution_histogram",
                stat_cols=summary_hits[:8],
            )

        requested = mapping.get("numeric_columns")
        skipped: List[str] = []
        if requested:
            cols: List[str] = []
            for c in requested:
                if c in df.columns and pd.api.types.is_numeric_dtype(df[c]):
                    cols.append(c)
                else:
                    skipped.append(c)
        else:
            cols = [c for c in df.columns
                    if pd.api.types.is_numeric_dtype(df[c])
                    and c != "__row_id__"]
        if not cols:
            raise OperatorInputError(
                "NO_NUMERIC_COLUMNS",
                solver="distribution_histogram",
            )

        # Weight column resolution.  ``weight_col`` is also excluded from
        # the column list to avoid "histogramming the weights themselves".
        weight_col = mapping.get("weight_col")
        if weight_col is not None:
            if (weight_col not in df.columns
                    or not pd.api.types.is_numeric_dtype(df[weight_col])):
                raise OperatorInputError(
                    "WEIGHT_COL_INVALID",
                    solver="distribution_histogram",
                    col=weight_col,
                    observed_dtype=(
                        str(df[weight_col].dtype)
                        if weight_col in df.columns else "absent"
                    ),
                )
            cols = [c for c in cols if c != weight_col]
            if not cols:
                raise OperatorInputError(
                    "NO_NUMERIC_COLUMNS",
                    solver="distribution_histogram",
                )

        # bin_range static_param (passed via constructor).
        bin_range = self.bin_range
        if bin_range is not None:
            try:
                lo, hi = float(bin_range[0]), float(bin_range[1])
                if not (lo < hi):
                    raise ValueError("bin_range low must be < high")
                hist_range: Optional[Tuple[float, float]] = (lo, hi)
            except Exception as e:
                # bin_range 是 static_param，不是用户列；不走 OperatorInputError
                # 而是直接 raise 让 runner 走 UNCAUGHT_EXCEPTION
                raise ValueError(
                    f"distribution_histogram: invalid bin_range "
                    f"{bin_range!r}: {e}"
                )
        else:
            hist_range = None

        rows = []
        for c in cols:
            v = df[c]
            if weight_col is not None:
                w = df[weight_col]
                mask = v.notna() & w.notna()
                s = v[mask].astype(float).values
                wv = w[mask].astype(float).values
            else:
                s = v.dropna().astype(float).values
                wv = None
            if len(s) == 0:
                continue
            # numpy.histogram 接受 weights= 参数：每个样本不再算 1 次
            # 而是按权重累加 → 频数表加权直方图免费拿到
            counts, edges = np.histogram(
                s, bins=self.n_bins, range=hist_range, weights=wv,
            )
            total = counts.sum()
            density = counts / total if total > 0 else counts * 0.0
            # 保持 unweighted 时 count 是 int（兼容老 schema）；
            # weighted 时只能是 float（sum of weights）
            count_cast = (lambda x: float(x)) if wv is not None else int
            for i in range(len(counts)):
                rows.append({
                    "column":    c,
                    "bin_index": i,
                    "bin_left":  float(edges[i]),
                    "bin_right": float(edges[i + 1]),
                    "count":     count_cast(counts[i]),
                    "density":   float(density[i]),
                })
        out = pd.DataFrame(rows)
        path = Path(output_dir) / HIST_CONTRACT.output_files["hist_csv"]
        out.to_csv(path, index=False)
        result: Dict[str, Any] = {"hist_csv": str(path),
                                    "hist_df": out,
                                    "n_columns": len(cols),
                                    "n_bins": self.n_bins,
                                    "weighted": weight_col is not None}
        if skipped:
            result["skipped_columns"] = skipped
        if bin_range is not None:
            result["bin_range"] = list(bin_range)
        return result


def get_describe_solver():     return DescribeFullSolver()
def get_histogram_solver(
    n_bins: int = 20,
    bin_range: Optional[Tuple[float, float]] = None,
) -> "DistributionHistogramSolver":
    return DistributionHistogramSolver(n_bins=n_bins, bin_range=bin_range)


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """describe_full vs numpy / scipy reference; histogram vs np.histogram.

    中文：两个 sub-solver 的"出厂自检"。

    Fixture：500 行 × 2 列
      - x_normal  ~ N(0, 1)        固定 seed=2026
      - x_uniform ~ U(0, 10)

    通过判定：
      - describe_full：mean / std / skew / kurtosis 与 pandas + scipy
        独立调用结果完全一致（绝对误差 < 1e-9）
      - distribution_histogram：每列 count 之和 = 500（n），bin 数 = 10
    """
    import tempfile

    rng = np.random.default_rng(2026)
    n = 500
    df = pd.DataFrame({
        "x_normal": rng.normal(0, 1, n),
        "x_uniform": rng.uniform(0, 10, n),
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # describe_full
        out = get_describe_solver().run(
            df=df, mapping=ColumnMapping({}), output_dir=tmp)
        s = out["stats_df"].set_index("column")
        for col in ("x_normal", "x_uniform"):
            ref_mean = float(df[col].mean())
            ref_std  = float(df[col].std(ddof=1))
            ref_skew = float(sps.skew(df[col].values))
            ref_kurt = float(sps.kurtosis(df[col].values))
            if abs(s.loc[col, "mean"] - ref_mean) > 1e-9:
                diffs.append(f"{col}.mean: solver {s.loc[col, 'mean']} vs "
                             f"pandas {ref_mean}")
            if abs(s.loc[col, "std"] - ref_std) > 1e-9:
                diffs.append(f"{col}.std mismatch")
            if abs(s.loc[col, "skewness"] - ref_skew) > 1e-9:
                diffs.append(f"{col}.skew vs scipy mismatch")
            if abs(s.loc[col, "kurtosis"] - ref_kurt) > 1e-9:
                diffs.append(f"{col}.kurt vs scipy mismatch")

        # histogram
        out2 = get_histogram_solver(n_bins=10).run(
            df=df, mapping=ColumnMapping({}), output_dir=tmp)
        h = out2["hist_df"]
        # total count per column should equal n
        per_col = h.groupby("column")["count"].sum()
        for col in ("x_normal", "x_uniform"):
            if int(per_col[col]) != n:
                diffs.append(f"{col} histogram total {per_col[col]} != {n}")
        # bin count per column = 10
        per_col_bins = h.groupby("column").size()
        for col in ("x_normal", "x_uniform"):
            if int(per_col_bins[col]) != 10:
                diffs.append(f"{col} expected 10 bins, got "
                             f"{per_col_bins[col]}")
    return {"ok": len(diffs) == 0,
            "summary": ("describe_full matches pandas/scipy; histogram "
                        "row counts and bin counts match" if not diffs
                        else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["describe_full",
                                    "distribution_histogram"]}}
