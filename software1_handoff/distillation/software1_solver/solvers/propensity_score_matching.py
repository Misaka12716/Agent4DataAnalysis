"""Propensity-score matching solver (F05 / Q26 横向对比).

Estimate propensity (treatment | covariates) with logistic regression,
then 1-to-1 nearest-neighbor match (without replacement) on the
propensity score.  Pure sklearn + numpy.  Deterministic with fixed seed.

Output:
  - matched_pairs.csv : [treated_id, control_id, treated_pscore, control_pscore]
  - balance_after.csv : per-covariate standardized mean diff before vs after

中文说明
========
倾向性评分匹配（PSM, Propensity Score Matching），用于观察性研究的
"伪随机化"对照（Q26 横向对比的常见前置）。

流程
----
1. **propensity model**：对协变量做 StandardScaler，用 LogisticRegression
   拟合 P(treatment=1 | X)；输出每个个体的 propensity score p ∈ (0, 1)
2. **1:1 nearest-neighbor matching**（贪心、不放回）：
   - 把所有 treated 个体随机打乱顺序（seed 控制）
   - 每个 treated 找剩下 control 中 |p_diff| 最小的那个
   - 可选 caliper：如果最小 |p_diff| > caliper 则该 treated 不匹配
3. **balance table**：每个协变量算 SMD（Standardized Mean Diff）
   - SMD = (mean_T - mean_C) / sqrt((var_T + var_C) / 2)
   - 经验阈值 |SMD| < 0.1 视作"平衡良好"
   - 输出 before/after 对比，让用户看 PSM 是否真的把混杂消掉

输入约定
========
- ``id_col``             必填
- ``treatment_col``      必填，BINARY_TARGET（0=control, 1=treated）
- ``covariate_columns``  必填，要平衡的数值协变量列表
- 静态参数：random_state（默认 42）/ caliper（默认 None=不限）

输出
====
- ``matched_pairs.csv``：[treated_id, control_id, treated_pscore,
                          control_pscore, abs_pscore_diff]
- ``balance_after.csv``：[covariate, smd_before, smd_after, balance_ok]
- 返回 dict 还含 ``n_pairs`` / ``max_abs_smd_after`` 便于上层判定
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - 三个 role 都必填
#   - static_params:
#       random_state  控制 LR + treated 顺序打乱
#       caliper       p 差距上限；None=不限（贪心总能匹配上）
#                     设成 0.05 = 严格匹配（可能丢掉一些 treated）
CONTRACT = SolverContract(
    name="propensity_score_matching",
    capability="F05_propensity_matched_comparison",
    description=(
        "1-to-1 nearest-neighbor PSM on logistic-regression propensity. "
        "Output: matched_pairs.csv + per-covariate SMD balance table."),
    roles={
        "id_col":             RoleSpec(Role.ID, "subject identifier"),
        "treatment_col":      RoleSpec(Role.BINARY_TARGET,
                                        "0 = control, 1 = treated"),
        "covariate_columns":  RoleSpec(Role.NUMERIC_LIST,
                                        "the numeric covariates to balance"),
    },
    static_params={"random_state": 42, "caliper": None},
    output_files={
        "matched_pairs_csv": "matched_pairs.csv",
        "balance_csv":       "balance_after.csv",
    },
)


def _smd(a, b):
    """中文：标准化均值差 SMD。

    公式：SMD = (mean_a - mean_b) / sqrt((var_a + var_b) / 2)
    用 ddof=1（样本方差）；分母为 0（两组完全相同的常数列）→ 返回 0
    经验阈值：|SMD| < 0.1 视作"平衡良好"。
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    pooled = np.sqrt((np.var(a, ddof=1) + np.var(b, ddof=1)) / 2.0)
    if pooled == 0:
        return 0.0
    return float((np.mean(a) - np.mean(b)) / pooled)


class PropensityScoreMatchingSolver:
    contract = CONTRACT

    def __init__(self, random_state: int = 42, caliper=None):
        """中文：

        :param random_state: 控制 LR 训练 + treated 打乱顺序，可复现。
        :param caliper:      最大允许的 p 差距；None = 贪心总能配上
                             (但可能配出很差的 pair)；0.05 是常用经验
                             阈值（5% 的 propensity 差距上限）。
        """
        self.random_state = random_state
        self.caliper = caliper

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping["id_col"]
        t_col = mapping["treatment_col"]
        covs: List[str] = list(mapping["covariate_columns"])

        sub = df[[id_col, t_col] + covs].dropna().reset_index(drop=True)
        T = sub[t_col].astype(int).to_numpy()
        X = sub[covs].astype(float).to_numpy()

        # 1) propensity model：用 LR 估 P(treatment | X)
        # scaler 是必要的（LR 对尺度不敏感但收敛更快、coef 也更可解释）
        scaler = StandardScaler().fit(X)
        Xs = scaler.transform(X)
        lr = LogisticRegression(max_iter=1000,
                                 random_state=self.random_state).fit(Xs, T)
        # p[i] = 个体 i 的 propensity score，用于后续匹配距离
        p = lr.predict_proba(Xs)[:, 1]

        treated = np.where(T == 1)[0]
        control = np.where(T == 0)[0].tolist()
        # 2) 贪心 1:1 nearest neighbor 匹配（无放回）
        # 把 treated 顺序打乱：避免"按数据原顺序匹配"导致的系统偏差
        rng = np.random.default_rng(self.random_state)
        order = treated.copy()
        rng.shuffle(order)

        pairs: List[Dict[str, Any]] = []
        used = set()
        for ti in order:
            if not control:
                break
            ti_p = p[ti]
            best, best_d = None, np.inf
            # 在剩下的 control 里找 |Δp| 最小的；caliper 限制最大可接受
            # 距离（None = 不限）
            for ci in control:
                if ci in used:
                    continue
                d = abs(p[ci] - ti_p)
                if (self.caliper is None or d <= self.caliper) and d < best_d:
                    best, best_d = ci, d
            # 没有人在 caliper 内 → 该 treated 不匹配（直接 drop，
            # 不会进入 pairs）
            if best is None:
                continue
            used.add(best)
            pairs.append({
                "treated_id": str(sub.loc[ti, id_col]),
                "control_id": str(sub.loc[best, id_col]),
                "treated_pscore": float(ti_p),
                "control_pscore": float(p[best]),
                "abs_pscore_diff": float(best_d),
            })

        pairs_df = pd.DataFrame(pairs)
        pp = Path(output_dir) / CONTRACT.output_files["matched_pairs_csv"]
        pairs_df.to_csv(pp, index=False)

        # 3) balance table：每个协变量在匹配前 / 后的 SMD
        # before：所有 treated vs 所有 control（混杂未消）
        # after ：仅 matched treated vs matched control（伪随机化后）
        # balance_ok 经验阈值 |SMD| < 0.1（cobalt R package 默认）
        rows = []
        treated_idx = treated
        all_control_idx = np.where(T == 0)[0]
        matched_treated = sub[sub[id_col].isin(pairs_df["treated_id"])].index
        matched_control = sub[sub[id_col].isin(pairs_df["control_id"])].index
        for c in covs:
            smd_before = _smd(sub.loc[treated_idx, c], sub.loc[all_control_idx, c])
            smd_after = _smd(sub.loc[matched_treated, c], sub.loc[matched_control, c])
            rows.append({
                "covariate":    c,
                "smd_before":   smd_before,
                "smd_after":    smd_after,
                "balance_ok":   abs(smd_after) < 0.1,
            })
        bal_df = pd.DataFrame(rows)
        bp = Path(output_dir) / CONTRACT.output_files["balance_csv"]
        bal_df.to_csv(bp, index=False)

        return {
            "matched_pairs_csv": str(pp),
            "balance_csv":       str(bp),
            "n_pairs":           int(len(pairs_df)),
            "balance_df":        bal_df,
            "max_abs_smd_after": float(bal_df["smd_after"].abs().max())
                                  if not bal_df.empty else 0.0,
        }


def get_solver(random_state: int = 42, caliper=None):
    return PropensityScoreMatchingSolver(random_state=random_state,
                                          caliper=caliper)


# ---------------------------------------------------------------------------
# Selftest: synthetic confounded study; PSM should reduce all SMDs below
# the unmatched baseline.
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """中文：合成的混杂研究 fixture，验证 PSM 能减少协变量的 SMD。

    Fixture：500 个体（seed=2026）
      - age      ~ U(20, 70)
      - bmi      ~ N(25, 4²)
      - severity ~ N(50, 10²)
      - logit(treatment) = 0.05·(age-45) + 0.05·(severity-50) + N(0, 1)
      - treatment = 1[logit > 0]
      → 治疗组的 age/severity 系统性偏高（混杂明显），bmi 与治疗
        无关（应该几乎无 SMD）

    通过判定：
      - PSM 后 age 和 severity 的 |SMD| 严格小于 PSM 前
        （核心：算法应该把可识别混杂消掉）
      - 至少匹配出 n/4 = 125 对（caliper=None 默认应该几乎全配上）
    """
    import tempfile

    rng = np.random.default_rng(2026)
    n = 500
    age = rng.uniform(20, 70, n)
    bmi = rng.normal(25, 4, n)
    severity = rng.normal(50, 10, n)
    # treatment depends on age + severity → confounded
    logit = 0.05 * (age - 45) + 0.05 * (severity - 50) + rng.normal(0, 1, n)
    t = (logit > 0).astype(int)
    df = pd.DataFrame({
        "PatientID": [f"P{i}" for i in range(n)],
        "treatment": t,
        "age": age, "bmi": bmi, "severity": severity,
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out = get_solver(random_state=42).run(
            df=df,
            mapping=ColumnMapping({
                "id_col": "PatientID",
                "treatment_col": "treatment",
                "covariate_columns": ["age", "bmi", "severity"],
            }),
            output_dir=tmp,
        )
        bal = out["balance_df"].set_index("covariate")
        for cov in ("age", "severity"):
            before = abs(float(bal.loc[cov, "smd_before"]))
            after = abs(float(bal.loc[cov, "smd_after"]))
            if after >= before:
                diffs.append(f"PSM did not reduce SMD for {cov!r}: "
                             f"before={before:.3f} after={after:.3f}")

        if out["n_pairs"] < n // 4:
            diffs.append(f"PSM produced too few pairs: {out['n_pairs']}")

    return {
        "ok": len(diffs) == 0,
        "summary": ("PSM reduces confounder SMD vs unmatched baseline"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["propensity_score_matching"]},
    }
