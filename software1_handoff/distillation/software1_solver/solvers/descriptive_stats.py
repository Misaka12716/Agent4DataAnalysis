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
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


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
)


class DescribeFullSolver:
    contract = DESCRIBE_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        cols = mapping.get("numeric_columns") or [
            c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
        ]
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
        return {"stats_csv": str(path),
                "stats_df": out,
                "n_columns": len(cols)}


# ---------------------------------------------------------------------------
# distribution_histogram
# ---------------------------------------------------------------------------
# Contract 说明：
#   - numeric_columns optional：不传就所有数值列
#   - static_params.n_bins 默认 20（经验值；20 个 bin 在常规连续变量
#     上既能看出形状又不会过拟合到锯齿）
HIST_CONTRACT = SolverContract(
    name="distribution_histogram",
    capability="F02_descriptive_stats_distribution",
    description=(
        "Per-column equal-width histogram with `n_bins` bins.  Output: "
        "long-format csv [column, bin_left, bin_right, count, density]."),
    roles={
        "numeric_columns": RoleSpec(Role.NUMERIC_LIST,
                                     "numeric columns to histogram",
                                     optional=True),
    },
    static_params={"n_bins": 20},
    output_files={"hist_csv": "distribution_histogram.csv"},
)


class DistributionHistogramSolver:
    contract = HIST_CONTRACT

    def __init__(self, n_bins: int = 20):
        """中文：

        :param n_bins: 等宽直方图 bin 数。默认 20 是描述性分析的经验
                       值；样本量极小（<50）时考虑 10，巨量（>10000）
                       时可以加到 50。
        """
        self.n_bins = n_bins

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        cols = mapping.get("numeric_columns") or [
            c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
        ]
        rows = []
        for c in cols:
            s = df[c].dropna().astype(float).values
            if len(s) == 0:
                continue
            # numpy.histogram 默认等宽 bin，edges 长度比 counts 大 1
            counts, edges = np.histogram(s, bins=self.n_bins)
            # density 这里是"相对频率"= counts / total（不是真概率密度）；
            # 全 0（s 退化）保护：counts*0.0 而不是 counts/0
            density = counts / counts.sum() if counts.sum() else counts * 0.0
            for i in range(len(counts)):
                rows.append({
                    "column":    c,
                    "bin_index": i,
                    "bin_left":  float(edges[i]),
                    "bin_right": float(edges[i + 1]),
                    "count":     int(counts[i]),
                    "density":   float(density[i]),
                })
        out = pd.DataFrame(rows)
        path = Path(output_dir) / HIST_CONTRACT.output_files["hist_csv"]
        out.to_csv(path, index=False)
        return {"hist_csv": str(path),
                "hist_df": out,
                "n_columns": len(cols),
                "n_bins": self.n_bins}


def get_describe_solver():     return DescribeFullSolver()
def get_histogram_solver(n_bins: int = 20): return DistributionHistogramSolver(n_bins=n_bins)


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
