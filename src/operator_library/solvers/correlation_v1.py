"""DEPRECATED — V8 Phase-2 rollback snapshot.  Do not import.

The live module is ``correlation.py``.  This file preserves the
pre-§2.4 implementation in case structured input validation regresses
something.  Safe to delete after two stable benchmark cycles.

---

Correlation solvers (F03 / Q08 / Q30).

Pearson (linear, parametric) and Spearman (rank, non-parametric)
correlation matrices over a list of numeric columns.  Backed by
``scipy.stats``.

中文说明
========
相关性分析三件套，全部基于 ``scipy.stats``。

| solver   | 算法                | 假设           | 适用                       |
|----------|---------------------|----------------|----------------------------|
| pearson  | scipy.pearsonr      | 线性 + 正态     | 连续变量、近似正态、关系线性 |
| spearman | scipy.spearmanr     | 单调（不必线性） | 序数变量、有 outlier、形状未知 |
| kendall  | scipy.kendalltau    | 单调，秩一致性    | 小样本 + tie 多、最稳健 |

输出统一是两份文件：
- ``matrix_csv``：对称矩阵，**对角线 = 1.0**（自相关）
- ``pairs_csv``：长表，列 [col_a, col_b, stat, p_value, n]，**只存
  上三角对（i<j）**，避免重复和对称冗余
- ``stat`` 列名按算法换：r / rho / tau

输入约定
========
- ``numeric_columns``：必填，要做两两相关的数值列名列表（≥2 列）
- 自动 dropna 后 **取两列共有的非空索引**（pairwise complete obs）
- 共有样本 < 3 → stat=NaN, p=NaN, n 仍记录便于审核

自由度
======
Pearson r 的 t 统计量自由度 = n - 2（共有样本数减 2）；
scipy.pearsonr 已经按这个 N 算出 p 值，我们的 ``n`` 列报的就是
共有样本数，可与 r 的统计推断口径对应。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
from scipy import stats

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# ---------------------------------------------------------------------------
# Pearson
# ---------------------------------------------------------------------------
# Contract 说明（三个 solver 一致）：
#   - numeric_columns 必填：列名列表，长度 ≥ 2
#   - 没有 static_params：算法本身没有可调超参
#   - 暴露给 LLM 的 role 含义：solver 只需要"一组数值列"，不区分
#     谁是因变量谁是自变量（相关性是对称的）
PEARSON_CONTRACT = SolverContract(
    name="pearson_correlation",
    capability="F03_correlation_analysis",
    description=(
        "Pairwise Pearson correlation r and p-value across a list of "
        "numeric columns.  Output: long-format csv "
        "[col_a, col_b, r, p_value, n] for upper-triangle pairs."),
    roles={
        "numeric_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "the numeric columns whose pairwise correlations to compute"),
    },
    output_files={"matrix_csv": "pearson_matrix.csv",
                  "pairs_csv":  "pearson_pairs.csv"},
)


SPEARMAN_CONTRACT = SolverContract(
    name="spearman_correlation",
    capability="F03_correlation_analysis",
    description=(
        "Pairwise Spearman rank correlation rho and p-value across a "
        "list of numeric columns.  Output: long-format csv "
        "[col_a, col_b, rho, p_value, n]."),
    roles={
        "numeric_columns": RoleSpec(Role.NUMERIC_LIST,
                                     "numeric columns to rank-correlate"),
    },
    output_files={"matrix_csv": "spearman_matrix.csv",
                  "pairs_csv":  "spearman_pairs.csv"},
)


KENDALL_CONTRACT = SolverContract(
    name="kendall_correlation",
    capability="F03_correlation_analysis",
    description=(
        "Pairwise Kendall tau (rank concordance) correlation across a "
        "list of numeric columns.  More robust to outliers than Spearman."),
    roles={
        "numeric_columns": RoleSpec(Role.NUMERIC_LIST,
                                     "numeric columns to rank-correlate"),
    },
    output_files={"matrix_csv": "kendall_matrix.csv",
                  "pairs_csv":  "kendall_pairs.csv"},
)


def _pairs(df, cols, fn) -> pd.DataFrame:
    # 只算上三角（i < j），避免对称冗余；fn 是 scipy.pearsonr /
    # spearmanr / kendalltau 中的任一个
    rows = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            # pairwise complete obs：每对单独 dropna，再取两列共有的索引
            # 这样不会因为某第 3 列全空就把 a/b 的可用样本一起砍掉
            x = df[a].astype(float).dropna()
            y = df[b].astype(float).dropna()
            common = x.index.intersection(y.index)
            if len(common) < 3:
                # 共有样本太少，p 值无意义；仍保留 n 便于审核
                rows.append({"col_a": a, "col_b": b,
                             "stat": np.nan, "p_value": np.nan,
                             "n": len(common)})
                continue
            stat, p = fn(x.loc[common].values, y.loc[common].values)
            rows.append({"col_a": a, "col_b": b,
                         "stat": float(stat), "p_value": float(p),
                         "n": int(len(common))})
    return pd.DataFrame(rows)


class PearsonCorrelationSolver:
    contract = PEARSON_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        cols = mapping["numeric_columns"]
        # 长表：上三角 (i<j) 的 (col_a, col_b, r, p_value, n)
        long = _pairs(df, cols, stats.pearsonr).rename(
            columns={"stat": "r"})
        long.to_csv(Path(output_dir) / PEARSON_CONTRACT.output_files["pairs_csv"],
                    index=False)
        # 矩阵：先用 np.eye 初始化（对角线 = 1.0 = 自相关），再把
        # 上三角的 r 镜像到下三角，保证矩阵严格对称
        m = pd.DataFrame(np.eye(len(cols)), index=cols, columns=cols)
        for _, row in long.iterrows():
            m.loc[row["col_a"], row["col_b"]] = row["r"]
            m.loc[row["col_b"], row["col_a"]] = row["r"]
        m_path = Path(output_dir) / PEARSON_CONTRACT.output_files["matrix_csv"]
        m.to_csv(m_path)
        return {"matrix_csv": str(m_path),
                "pairs_csv": str(Path(output_dir) / PEARSON_CONTRACT.output_files["pairs_csv"]),
                "pairs_df": long}


class SpearmanCorrelationSolver:
    contract = SPEARMAN_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        cols = mapping["numeric_columns"]
        long = _pairs(df, cols, stats.spearmanr).rename(
            columns={"stat": "rho"})
        long.to_csv(Path(output_dir) / SPEARMAN_CONTRACT.output_files["pairs_csv"],
                    index=False)
        m = pd.DataFrame(np.eye(len(cols)), index=cols, columns=cols)
        for _, row in long.iterrows():
            m.loc[row["col_a"], row["col_b"]] = row["rho"]
            m.loc[row["col_b"], row["col_a"]] = row["rho"]
        m_path = Path(output_dir) / SPEARMAN_CONTRACT.output_files["matrix_csv"]
        m.to_csv(m_path)
        return {"matrix_csv": str(m_path),
                "pairs_csv": str(Path(output_dir) / SPEARMAN_CONTRACT.output_files["pairs_csv"]),
                "pairs_df": long}


class KendallCorrelationSolver:
    contract = KENDALL_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        cols = mapping["numeric_columns"]
        long = _pairs(df, cols, stats.kendalltau).rename(
            columns={"stat": "tau"})
        long.to_csv(Path(output_dir) / KENDALL_CONTRACT.output_files["pairs_csv"],
                    index=False)
        m = pd.DataFrame(np.eye(len(cols)), index=cols, columns=cols)
        for _, row in long.iterrows():
            m.loc[row["col_a"], row["col_b"]] = row["tau"]
            m.loc[row["col_b"], row["col_a"]] = row["tau"]
        m_path = Path(output_dir) / KENDALL_CONTRACT.output_files["matrix_csv"]
        m.to_csv(m_path)
        return {"matrix_csv": str(m_path),
                "pairs_csv": str(Path(output_dir) / KENDALL_CONTRACT.output_files["pairs_csv"]),
                "pairs_df": long}


def get_pearson_solver():  return PearsonCorrelationSolver()
def get_spearman_solver(): return SpearmanCorrelationSolver()
def get_kendall_solver():  return KendallCorrelationSolver()


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """Verify both solvers against an analytic-perfect-correlation
    fixture and against numpy.corrcoef as an independent reference.

    中文：3 个 sub-solver 的"出厂自检"。

    Fixture：60 行（seed=42）
      - x ~ N(0, 1)
      - y = 0.7·x + N(0, 0.5²)   → 期望 |corr(x,y)| ≈ 0.8
      - z = -x                    → 期望 corr(x, z) = -1.0 严格

    通过判定（误差 < 1e-9）：
      - Pearson (x, z) = -1.0；Pearson (x, y) 与 numpy.corrcoef 一致
      - Spearman (x, z) = -1.0（rank 对单调下降也是 -1）
      - Kendall  (x, z) = -1.0（完美反 concordant）
                  (x, y) 与独立调用 scipy.kendalltau 一致
    """
    import tempfile

    rng = np.random.default_rng(42)
    n = 60
    x = rng.normal(size=n)
    y = 0.7 * x + rng.normal(scale=0.5, size=n)   # ~0.8 correlation
    z = -x                                          # exact -1.0

    df = pd.DataFrame({"x": x, "y": y, "z": z})

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # --- Pearson ----
        out = get_pearson_solver().run(
            df=df,
            mapping=ColumnMapping({"numeric_columns": ["x", "y", "z"]}),
            output_dir=tmp,
        )
        pairs = out["pairs_df"].set_index(["col_a", "col_b"])
        # Reference: numpy.corrcoef
        ref = np.corrcoef(df[["x", "y", "z"]].T.values)
        if abs(pairs.loc[("x", "z"), "r"] - (-1.0)) > 1e-9:
            diffs.append(f"pearson(x,z) expected -1.0, got "
                         f"{pairs.loc[('x','z'), 'r']}")
        if abs(pairs.loc[("x", "y"), "r"] - ref[0, 1]) > 1e-9:
            diffs.append(f"pearson(x,y) {pairs.loc[('x','y'), 'r']} "
                         f"diverges from numpy.corrcoef {ref[0, 1]}")

        # --- Spearman ----
        out2 = get_spearman_solver().run(
            df=df,
            mapping=ColumnMapping({"numeric_columns": ["x", "y", "z"]}),
            output_dir=tmp,
        )
        s_pairs = out2["pairs_df"].set_index(["col_a", "col_b"])
        # Reference: scipy.stats.spearmanr called twice (we already use it,
        # so just verify the perfect-rank case where spearman=−1 exactly).
        if abs(s_pairs.loc[("x", "z"), "rho"] - (-1.0)) > 1e-9:
            diffs.append(f"spearman(x,z) expected -1.0, got "
                         f"{s_pairs.loc[('x','z'), 'rho']}")

        # --- Kendall ----
        out3 = get_kendall_solver().run(
            df=df,
            mapping=ColumnMapping({"numeric_columns": ["x", "y", "z"]}),
            output_dir=tmp,
        )
        k_pairs = out3["pairs_df"].set_index(["col_a", "col_b"])
        # x and -x are perfectly anti-concordant → kendall tau = -1
        if abs(k_pairs.loc[("x", "z"), "tau"] - (-1.0)) > 1e-9:
            diffs.append(f"kendall(x,z) expected -1.0, got "
                         f"{k_pairs.loc[('x','z'), 'tau']}")
        # cross-check kendall(x,y) vs scipy.kendalltau directly
        ref_tau, _ = stats.kendalltau(df["x"], df["y"])
        if abs(k_pairs.loc[("x", "y"), "tau"] - float(ref_tau)) > 1e-9:
            diffs.append(f"kendall(x,y) {k_pairs.loc[('x','y'), 'tau']} "
                         f"diverges from scipy {ref_tau}")

    return {
        "ok": len(diffs) == 0,
        "summary": ("pearson + spearman + kendall match analytic / "
                    "independent reference" if not diffs
                    else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["pearson_correlation",
                                "spearman_correlation",
                                "kendall_correlation"]},
    }
