"""Normality test solver (F02 / Q34).

For each numeric column, compute Shapiro-Wilk W/p and a Kolmogorov-
Smirnov D/p against the fitted N(mean, std).  Returns a deliverable csv
with columns
    [column, shapiro_W, shapiro_p, ks_D, ks_p, is_normal_alpha_0.05]
plus a ``stats_dict`` with per-column results for json-assertion checks.

Why a custom solver: the audit had Q34 as MISSING — no csv operator for
normality.  This solver uses scipy.stats and is the new authoritative
implementation for Q34.

中文说明
========
正态性检验。对指定的每一个数值列同时跑两种 test：

- **Shapiro-Wilk** （``scipy.stats.shapiro``）
  W ∈ (0, 1]，越接近 1 越正态；适合小样本（n ≤ 5000）
- **Kolmogorov-Smirnov** vs 拟合的 N(mean, std)
  （``scipy.stats.kstest``，``args=(mu, sigma)``）
  D 是经验 CDF 与正态 CDF 的最大距离；样本越大越敏感

只有 **两个 test 的 p 都 > alpha** 才判为正态（更严格的 AND 逻辑）。

输入 / 输出
==========
- ``test_columns``：必填，要检验的数值列列表
- ``alpha`` 默认 0.05
- 输出 ``results_csv`` = ``normality_results.csv``：
  [column, shapiro_W, shapiro_p, ks_D, ks_p, is_normal_alpha_0.05]
- 同时返回 ``stats_dict`` 给 runner 的 json 断言比对用

边缘 case 处理：
- n < 3 → 全 NaN，is_normal=False（Shapiro-Wilk 至少需要 3 个样本）
- sigma == 0（全相同值）→ ks 退化成 (D=1, p=0)，避免除零
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
from scipy import stats

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - test_columns 必填：明确要检验哪些列（不像 describe_full 会
#     fallback 到所有数值列；正态性检验通常只关心目标变量）
#   - static_params.alpha：默认 0.05；调成 0.01 = 更宽松地判正态
CONTRACT = SolverContract(
    name="normality_test",
    capability="F02_descriptive_stats_distribution",
    description=(
        "Shapiro-Wilk + Kolmogorov-Smirnov normality tests per numeric "
        "column (AND rule: both p > alpha → normal).  test_columns "
        "omitted → all numeric columns.  Output: csv [column, shapiro_W, "
        "shapiro_p, ks_D, ks_p, is_normal_alpha_0.05]."
    ),
    roles={
        "test_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "numeric columns to test (omit → all numeric)",
            optional=True,
        ),
    },
    static_params={"alpha": 0.05},
    output_files={"results_csv": "normality_results.csv"},
    output_kind={"results_csv": "s"},
)


class NormalityTestSolver:
    contract = CONTRACT

    def __init__(self, alpha: float = 0.05):
        """中文：

        :param alpha: 显著性水平。p > alpha 视作"无法拒绝正态"。
                      默认 0.05；学术论文一般也是 0.05，临床敏感场景
                      可以收紧到 0.01。
        """
        self.alpha = alpha

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        requested = mapping.get("test_columns")
        if requested:
            cols = [c for c in requested
                    if c in df.columns
                    and pd.api.types.is_numeric_dtype(df[c])]
        else:
            cols = [c for c in df.columns
                    if pd.api.types.is_numeric_dtype(df[c])]
        if not cols:
            raise ValueError("normality_test: no usable numeric columns")
        rows = []
        per_col: Dict[str, Dict[str, Any]] = {}
        for c in cols:
            x = df[c].dropna().to_numpy(dtype=float)
            # Shapiro-Wilk 至少要 3 个样本；不够直接全 NaN 不正态
            if len(x) < 3:
                rows.append({
                    "column": c, "shapiro_W": np.nan, "shapiro_p": np.nan,
                    "ks_D": np.nan, "ks_p": np.nan,
                    "is_normal_alpha_0.05": False,
                })
                continue
            W, p_sw = stats.shapiro(x)
            # ddof=1：用样本标准差估总体；与 KS 拟合 N(mu, sigma) 的口径一致
            mu, sigma = float(np.mean(x)), float(np.std(x, ddof=1))
            if sigma == 0:
                # 退化情形：全部数值相同，KS 不能除以 0；硬编码
                # D=1, p=0 表示"完全不像连续正态"
                D, p_ks = 1.0, 0.0
            else:
                # kstest("norm", args=(mu, sigma)) 与拟合的正态做单样本 KS
                D, p_ks = stats.kstest(x, "norm", args=(mu, sigma))
            # AND 逻辑：两个 test 都通不过拒绝才视作正态（更严格）
            is_normal = bool(p_sw > self.alpha and p_ks > self.alpha)
            rows.append({
                "column": c,
                "shapiro_W": float(W),
                "shapiro_p": float(p_sw),
                "ks_D": float(D),
                "ks_p": float(p_ks),
                "is_normal_alpha_0.05": is_normal,
            })
            per_col[c] = {
                "shapiro_W": float(W),
                "shapiro_p": float(p_sw),
                "ks_D": float(D),
                "ks_p": float(p_ks),
                "is_normal_at_alpha_0.05": is_normal,
            }

        out_df = pd.DataFrame(rows)
        path = Path(output_dir) / CONTRACT.output_files["results_csv"]
        out_df.to_csv(path, index=False)

        # also expose a flat dict so the runner can do
        # compare_json_with_assertions against gt/normality_truth.json
        stats_dict: Dict[str, Any] = {"expected": per_col}
        return {"results_csv": str(path),
                "stats_dict": stats_dict}


def get_solver(alpha: float = 0.05):
    return NormalityTestSolver(alpha=alpha)


def selftest():
    """Verify against scipy.stats.shapiro on a known-normal and a
    known-skewed (exponential) sample with fixed seed.

    中文：fixture = 200 行 × 2 列（seed=42）：
      - normal_col ~ N(0, 1)        → 期望 is_normal=True
      - skewed_col ~ Exp(1)         → 期望 is_normal=False（指数分布
                                      重尾，p 应远 < 0.05）

    通过判定：
      - W / p 与独立调用 scipy.stats.shapiro 完全一致（误差 < 1e-9）
      - normal_col 被判正态、skewed_col 被判非正态
    """
    import tempfile

    rng = np.random.default_rng(42)
    n = 200
    x_normal = rng.normal(0, 1, n)
    x_skewed = rng.exponential(1.0, n)
    df = pd.DataFrame({"normal_col": x_normal, "skewed_col": x_skewed})
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(alpha=0.05).run(
            df=df,
            mapping=ColumnMapping({"test_columns": ["normal_col",
                                                       "skewed_col"]}),
            output_dir=Path(tmp),
        )
        per = out["stats_dict"]["expected"]
        # independent reference
        Wn, pn = stats.shapiro(x_normal)
        Ws, ps = stats.shapiro(x_skewed)
        if abs(per["normal_col"]["shapiro_W"] - float(Wn)) > 1e-9:
            diffs.append("normal_col W mismatch vs scipy")
        if abs(per["normal_col"]["shapiro_p"] - float(pn)) > 1e-9:
            diffs.append("normal_col p mismatch vs scipy")
        if abs(per["skewed_col"]["shapiro_W"] - float(Ws)) > 1e-9:
            diffs.append("skewed_col W mismatch vs scipy")
        if not per["normal_col"]["is_normal_at_alpha_0.05"]:
            diffs.append("normal_col should be classified normal")
        if per["skewed_col"]["is_normal_at_alpha_0.05"]:
            diffs.append("skewed_col should be classified non-normal")
    return {"ok": len(diffs) == 0,
            "summary": ("normality results match scipy.shapiro on "
                        "known-normal / known-skewed fixtures"
                        if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs, "tested": ["normality_test"]}}
