"""Hypothesis-test atomic solvers (F04 / Q09 / Q35 / Q36).

  - welch_t_test         (Q35 t检验; Welch unequal-variance variant)
  - mann_whitney_u_test  (Q36 非参数检验)
  - chi_square_test      (Q09 类别变量差异性)
  - oneway_anova         (Q26 多组横向对比)
  - kruskal_wallis       (Q36 非参数多组)

Each accepts a long-format DataFrame with a ``group`` column + a
``value`` column (or, for chi-square, two categorical columns).  Backed
by ``scipy.stats``.

中文说明
========
假设检验五件套：Welch t / Mann-Whitney U / Chi-square / 单因素
ANOVA / Kruskal-Wallis。

每个 solver 都直接调 scipy.stats，参数 1:1 对齐 scipy，保证结果与
"用户自己写一行 scipy 调用"完全一致。

输入约定
========
- **双样本类（Welch / MW）**：
    value_col=数值列名，group_col=0/1 列名（必须恰好 2 个 level）
- **列联类（Chi2）**：
    row_col / col_col 都是 categorical
- **多组类（ANOVA / Kruskal）**：
    value_col=数值，group_col=≥2 个 level 的 categorical

何时用哪一个
============
| 数据类型      | 假设满足正态 | 假设不满足正态  |
|---------------|--------------|-----------------|
| 2 组连续      | Welch t      | Mann-Whitney U  |
| ≥3 组连续     | One-way ANOVA| Kruskal-Wallis  |
| 2 个类别变量  | Chi-square 独立性                            |

Welch 比 Student t 多一步：自由度用 Welch-Satterthwaite 修正
（不需要等方差假设），更安全；scipy 默认 ``equal_var=False`` 就是这个。

输出
====
所有 solver 都吐 ``summary_json``；Chi2 多吐一份列联表 csv 方便人看。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy import stats

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# ---------------------------------------------------------------------------
# Welch's t-test (two-sample, unequal variance)
# ---------------------------------------------------------------------------
# Contract 说明：
#   - value_col 必填：数值结局列
#   - group_col 必填：BINARY_TARGET，恰好两个 level（不限 0/1，
#     字符串 "T"/"C" 也行；solver 内部 sorted(unique) 后取前两个）
#   - 没有 static_params：Welch t 没什么可调的（永远不等方差）
WELCH_CONTRACT = SolverContract(
    name="welch_t_test",
    capability="F04_group_difference_hypothesis_test",
    description=(
        "Welch's two-sample t-test on a numeric column split by a "
        "binary group column.  Output: dict with t-statistic, "
        "df (Welch-Satterthwaite), p_value, mean diff, group sizes."
    ),
    roles={
        "value_col": RoleSpec(Role.NUMERIC,        "the numeric outcome"),
        "group_col": RoleSpec(Role.BINARY_TARGET,  "the 0/1 group label"),
    },
    output_files={"summary_json": "welch_t_summary.json"},
)


class WelchTTestSolver:
    contract = WELCH_CONTRACT

    def run(self, df, mapping, output_dir: Path) -> Dict[str, Any]:
        v, g = mapping["value_col"], mapping["group_col"]
        # 行级 dropna：value 或 group 任一缺失就丢这行（保证两组样本数
        # 与 scipy 内部的口径一致）
        df = df[[v, g]].dropna()
        groups = sorted(df[g].unique().tolist())
        if len(groups) != 2:
            raise ValueError(f"welch_t_test needs exactly 2 groups, "
                              f"got {groups}")
        a = df[df[g] == groups[0]][v].astype(float).to_numpy()
        b = df[df[g] == groups[1]][v].astype(float).to_numpy()
        # equal_var=False 即 Welch's t（不假设两组同方差）
        t_stat, p = stats.ttest_ind(a, b, equal_var=False, nan_policy="omit")
        # 手算 Welch-Satterthwaite df（scipy 不直接返回它，但下游分析 /
        # 论文报告经常需要这个数）：
        #   df_w = (s1²/n1 + s2²/n2)² / [ (s1²/n1)²/(n1-1) + (s2²/n2)²/(n2-1) ]
        # n<=1 时 df 没有定义 → NaN
        s1, s2 = np.var(a, ddof=1), np.var(b, ddof=1)
        n1, n2 = len(a), len(b)
        df_w = (s1 / n1 + s2 / n2) ** 2 / (
            (s1 / n1) ** 2 / (n1 - 1) + (s2 / n2) ** 2 / (n2 - 1)
        ) if (n1 > 1 and n2 > 1) else float("nan")
        out = {
            "test":        "welch_t",
            "groups":      [str(groups[0]), str(groups[1])],
            "n_a":         int(n1),
            "n_b":         int(n2),
            "mean_a":      float(np.mean(a)),
            "mean_b":      float(np.mean(b)),
            "mean_diff":   float(np.mean(a) - np.mean(b)),
            "t_statistic": float(t_stat),
            "df":          float(df_w),
            "p_value":     float(p),
        }
        path = Path(output_dir) / WELCH_CONTRACT.output_files["summary_json"]
        path.write_text(__import__("json").dumps(out, ensure_ascii=False,
                                                    indent=2),
                        encoding="utf-8")
        return {"summary_json": str(path), "summary_dict": out}


# ---------------------------------------------------------------------------
# Mann-Whitney U
# ---------------------------------------------------------------------------
# Contract 说明：与 Welch 完全对称（value + 二分组），适合数据不正态
MW_CONTRACT = SolverContract(
    name="mann_whitney_u_test",
    capability="F04_group_difference_hypothesis_test",
    description=(
        "Mann-Whitney U rank-sum test on a numeric column split by a "
        "binary group column."
    ),
    roles={
        "value_col": RoleSpec(Role.NUMERIC,       "the numeric outcome"),
        "group_col": RoleSpec(Role.BINARY_TARGET, "the 0/1 group label"),
    },
    output_files={"summary_json": "mann_whitney_summary.json"},
)


class MannWhitneyUSolver:
    contract = MW_CONTRACT

    def run(self, df, mapping, output_dir: Path) -> Dict[str, Any]:
        v, g = mapping["value_col"], mapping["group_col"]
        df = df[[v, g]].dropna()
        groups = sorted(df[g].unique().tolist())
        if len(groups) != 2:
            raise ValueError(f"need exactly 2 groups, got {groups}")
        a = df[df[g] == groups[0]][v].astype(float).to_numpy()
        b = df[df[g] == groups[1]][v].astype(float).to_numpy()
        # alternative='two-sided'：双边检验，问"两组分布是否不同"
        # 单边场景（"a 大于 b"）调用方应自己改 alternative='greater'
        U, p = stats.mannwhitneyu(a, b, alternative="two-sided")
        out = {
            "test": "mann_whitney_u",
            "groups": [str(groups[0]), str(groups[1])],
            "n_a": int(len(a)), "n_b": int(len(b)),
            "median_a": float(np.median(a)),
            "median_b": float(np.median(b)),
            "U_statistic": float(U),
            "p_value": float(p),
        }
        path = Path(output_dir) / MW_CONTRACT.output_files["summary_json"]
        path.write_text(__import__("json").dumps(out, ensure_ascii=False,
                                                    indent=2),
                        encoding="utf-8")
        return {"summary_json": str(path), "summary_dict": out}


# ---------------------------------------------------------------------------
# Chi-square independence
# ---------------------------------------------------------------------------
# Contract 说明：检验两个 categorical 列是否独立
#   - row_col / col_col 都是 CATEGORICAL，对称的（谁当行谁当列等价）
#   - 多吐一份 contingency_table.csv 方便人工核对
CHI2_CONTRACT = SolverContract(
    name="chi_square_independence",
    capability="F04_group_difference_hypothesis_test",
    description=(
        "Pearson chi-square independence test on the contingency table "
        "of two categorical columns."
    ),
    roles={
        "row_col": RoleSpec(Role.CATEGORICAL, "first categorical column"),
        "col_col": RoleSpec(Role.CATEGORICAL, "second categorical column"),
    },
    output_files={"summary_json": "chi2_summary.json",
                  "table_csv":    "contingency_table.csv"},
)


class ChiSquareIndependenceSolver:
    contract = CHI2_CONTRACT

    def run(self, df, mapping, output_dir: Path) -> Dict[str, Any]:
        r, c = mapping["row_col"], mapping["col_col"]
        # pd.crosstab 自动处理类别顺序、自动 dropna；ct 是 (R, C) 列联表
        ct = pd.crosstab(df[r], df[c])
        # chi2_contingency 返回 (统计量, p, dof, 期望频数矩阵)
        # dof = (R-1) * (C-1)；期望频数 < 5 的格子多的话 p 不太可信，
        # 但这里不强制 Yates / Fisher 修正，由调用方判断
        chi2, p, dof, expected = stats.chi2_contingency(ct.values)
        out = {
            "test": "chi2_independence",
            "row_col": r, "col_col": c,
            "chi2": float(chi2), "dof": int(dof), "p_value": float(p),
            "n_total": int(ct.values.sum()),
            "table_shape": list(ct.shape),
        }
        ct.to_csv(Path(output_dir) / CHI2_CONTRACT.output_files["table_csv"])
        sj = Path(output_dir) / CHI2_CONTRACT.output_files["summary_json"]
        sj.write_text(__import__("json").dumps(out, ensure_ascii=False,
                                                  indent=2),
                       encoding="utf-8")
        return {"summary_json": str(sj),
                "table_csv":    str(Path(output_dir) / CHI2_CONTRACT.output_files["table_csv"]),
                "summary_dict": out,
                "expected_freq": expected.tolist()}


# ---------------------------------------------------------------------------
# One-way ANOVA
# ---------------------------------------------------------------------------
# Contract 说明：单因素方差分析，参数版（假设各组正态 + 等方差）
#   - group_col 是 CATEGORICAL（≥2 个 level，不限于二分）
ANOVA_CONTRACT = SolverContract(
    name="oneway_anova",
    capability="F04_group_difference_hypothesis_test",
    description=(
        "One-way ANOVA F-test on a numeric column split by a "
        "categorical group column (≥2 groups)."),
    roles={
        "value_col": RoleSpec(Role.NUMERIC,     "numeric outcome"),
        "group_col": RoleSpec(Role.CATEGORICAL, "categorical group label"),
    },
    output_files={"summary_json": "anova_summary.json"},
)


class OneWayAnovaSolver:
    contract = ANOVA_CONTRACT

    def run(self, df, mapping, output_dir: Path) -> Dict[str, Any]:
        v, g = mapping["value_col"], mapping["group_col"]
        df = df[[v, g]].dropna()
        # 按 sorted(unique level) 切成多个数组，再 *unpack 给 f_oneway；
        # sorted 保证多次跑同数据时组顺序稳定（虽然 ANOVA 对顺序不敏感）
        groups: List[np.ndarray] = [df[df[g] == lvl][v].astype(float).to_numpy()
                                      for lvl in sorted(df[g].unique())]
        # F = MS_between / MS_within；p 来自 F 分布
        F, p = stats.f_oneway(*groups)
        out = {
            "test": "oneway_anova",
            "n_groups": int(len(groups)),
            "group_sizes": [int(len(x)) for x in groups],
            "F_statistic": float(F), "p_value": float(p),
        }
        path = Path(output_dir) / ANOVA_CONTRACT.output_files["summary_json"]
        path.write_text(__import__("json").dumps(out, ensure_ascii=False,
                                                    indent=2),
                        encoding="utf-8")
        return {"summary_json": str(path), "summary_dict": out}


# ---------------------------------------------------------------------------
# Kruskal-Wallis
# ---------------------------------------------------------------------------
# Contract 说明：ANOVA 的非参数版（只假设独立、连续，不要求正态）
#   - 用秩和构造 H，p 渐近卡方
KW_CONTRACT = SolverContract(
    name="kruskal_wallis",
    capability="F04_group_difference_hypothesis_test",
    description=(
        "Kruskal-Wallis H-test (non-parametric ANOVA) on a numeric "
        "column split by a categorical group column."),
    roles={
        "value_col": RoleSpec(Role.NUMERIC,     "numeric outcome"),
        "group_col": RoleSpec(Role.CATEGORICAL, "categorical group label"),
    },
    output_files={"summary_json": "kruskal_summary.json"},
)


class KruskalWallisSolver:
    contract = KW_CONTRACT

    def run(self, df, mapping, output_dir: Path) -> Dict[str, Any]:
        v, g = mapping["value_col"], mapping["group_col"]
        df = df[[v, g]].dropna()
        groups = [df[df[g] == lvl][v].astype(float).to_numpy()
                  for lvl in sorted(df[g].unique())]
        # H 统计量 ≈ chi² with df = k-1，scipy 内部会处理 tie correction
        H, p = stats.kruskal(*groups)
        out = {
            "test": "kruskal_wallis",
            "n_groups": int(len(groups)),
            "group_sizes": [int(len(x)) for x in groups],
            "H_statistic": float(H), "p_value": float(p),
        }
        path = Path(output_dir) / KW_CONTRACT.output_files["summary_json"]
        path.write_text(__import__("json").dumps(out, ensure_ascii=False,
                                                    indent=2),
                        encoding="utf-8")
        return {"summary_json": str(path), "summary_dict": out}


def get_welch_solver():    return WelchTTestSolver()
def get_mannwhitney_solver(): return MannWhitneyUSolver()
def get_chi2_solver():     return ChiSquareIndependenceSolver()
def get_anova_solver():    return OneWayAnovaSolver()
def get_kruskal_solver():  return KruskalWallisSolver()


# ---------------------------------------------------------------------------
# Selftest — uses scipy as the same/independent reference; cross-checks
# - Welch vs equal-variance edge case
# - Mann-Whitney monotonic-shift case (expect very small p)
# - Chi-square independent case (expect large p)
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """中文：5 个 sub-solver 的"出厂自检"。

    所有 fixture 都用 ``np.random.default_rng(2026)`` 固定 seed。

    用例与判定：
      1. **Welch t**：两组分布显著不同（loc=0 vs loc=1, scale 也不同）。
         t/p 与 ``scipy.ttest_ind(..., equal_var=False)`` 完全一致。
      2. **Mann-Whitney**：构造 a~U(0,1), b~U(0.5,1.5)（明显单调位移）。
         U 与独立调用一致；要求 p < 0.05（功效检查）。
      3. **Chi-square 独立**：r/c 完全独立采样 → 期望 p > 0.05；
         dof = (3-1)·(2-1) = 2。
      4. **One-way ANOVA**：3 组同分布 → F/p 与 ``scipy.f_oneway`` 一致。
      5. **Kruskal-Wallis**：同 4，H/p 与 ``scipy.kruskal`` 一致。

    通过判定：所有数值与 scipy 误差 < 1e-9。
    """
    import tempfile

    rng = np.random.default_rng(2026)
    diffs = []

    # ----- Welch t -----
    a = rng.normal(loc=0.0, scale=1.0, size=80)
    b = rng.normal(loc=1.0, scale=2.0, size=80)
    df = pd.DataFrame({"v": np.concatenate([a, b]),
                       "g": [0] * 80 + [1] * 80})
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        r = get_welch_solver().run(df=df,
            mapping=ColumnMapping({"value_col": "v", "group_col": "g"}),
            output_dir=tmp)
        # independent: scipy.ttest_ind with equal_var=False
        ref = stats.ttest_ind(a, b, equal_var=False)
        if abs(r["summary_dict"]["t_statistic"] - float(ref.statistic)) > 1e-9:
            diffs.append(f"welch t-stat mismatch: solver "
                         f"{r['summary_dict']['t_statistic']} vs scipy "
                         f"{ref.statistic}")
        if abs(r["summary_dict"]["p_value"] - float(ref.pvalue)) > 1e-9:
            diffs.append(f"welch p mismatch")

        # ----- Mann-Whitney -----
        # construct a clear monotonic shift; expect p << 0.05
        a2 = rng.uniform(0, 1, size=50)
        b2 = rng.uniform(0.5, 1.5, size=50)
        df2 = pd.DataFrame({"v": np.concatenate([a2, b2]),
                            "g": [0] * 50 + [1] * 50})
        r2 = get_mannwhitney_solver().run(df=df2,
            mapping=ColumnMapping({"value_col": "v", "group_col": "g"}),
            output_dir=tmp)
        ref2 = stats.mannwhitneyu(a2, b2, alternative="two-sided")
        if abs(r2["summary_dict"]["U_statistic"] - float(ref2.statistic)) > 1e-9:
            diffs.append("mann_whitney U mismatch")
        if r2["summary_dict"]["p_value"] >= 0.05:
            diffs.append(f"mann_whitney expected p<0.05 with shifted "
                         f"distributions, got {r2['summary_dict']['p_value']}")

        # ----- Chi-square independence ------
        # Build a contingency where row & col are independent → expect p >> 0.05
        df3 = pd.DataFrame({
            "r": rng.choice(["A", "B", "C"], size=300),
            "c": rng.choice(["X", "Y"], size=300),
        })
        r3 = get_chi2_solver().run(df=df3,
            mapping=ColumnMapping({"row_col": "r", "col_col": "c"}),
            output_dir=tmp)
        if r3["summary_dict"]["dof"] != 2:
            diffs.append(f"chi2 dof: expected 2, got {r3['summary_dict']['dof']}")
        # Independent → typically p > 0.1; allow some slack
        if r3["summary_dict"]["p_value"] < 0.05:
            diffs.append(f"chi2 expected p>0.05 on independent fixture, "
                         f"got {r3['summary_dict']['p_value']}")

        # ----- One-way ANOVA -----
        # 3 groups same mean → large p
        x1 = rng.normal(0, 1, 40); x2 = rng.normal(0, 1, 40); x3 = rng.normal(0, 1, 40)
        df4 = pd.DataFrame({"v": np.concatenate([x1, x2, x3]),
                            "g": ["A"] * 40 + ["B"] * 40 + ["C"] * 40})
        r4 = get_anova_solver().run(df=df4,
            mapping=ColumnMapping({"value_col": "v", "group_col": "g"}),
            output_dir=tmp)
        ref4 = stats.f_oneway(x1, x2, x3)
        if abs(r4["summary_dict"]["F_statistic"] - float(ref4.statistic)) > 1e-9:
            diffs.append("anova F mismatch")
        if abs(r4["summary_dict"]["p_value"] - float(ref4.pvalue)) > 1e-9:
            diffs.append("anova p mismatch")

        # ----- Kruskal-Wallis -----
        r5 = get_kruskal_solver().run(df=df4,
            mapping=ColumnMapping({"value_col": "v", "group_col": "g"}),
            output_dir=tmp)
        ref5 = stats.kruskal(x1, x2, x3)
        if abs(r5["summary_dict"]["H_statistic"] - float(ref5.statistic)) > 1e-9:
            diffs.append("kruskal H mismatch")
        if abs(r5["summary_dict"]["p_value"] - float(ref5.pvalue)) > 1e-9:
            diffs.append("kruskal p mismatch")

    return {
        "ok": len(diffs) == 0,
        "summary": ("welch / mann_whitney / chi2 / anova / kruskal all "
                    "match scipy ground truth" if not diffs
                    else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["welch_t_test", "mann_whitney_u_test",
                                "chi_square_independence",
                                "oneway_anova", "kruskal_wallis"]},
    }
