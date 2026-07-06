"""Multiple-comparison correction solver (F04 / Q37).

Given a column of raw p-values, compute Bonferroni and Benjamini-
Hochberg FDR adjusted p-values + significance flags.  Backed by
``statsmodels.stats.multitest.multipletests`` — fully deterministic.

Why a custom solver: Q37 was MISSING in the operator audit — no csv op
covers multiple correction.  This solver is the new authoritative
implementation.

中文说明
========
多重比较校正：把一组原始 p 值同时跑 **Bonferroni** 和
**Benjamini-Hochberg FDR**，输出校正后 p + 显著性 0/1 旗标。
完全基于 ``statsmodels.stats.multitest.multipletests``，确定性。

Bonferroni vs BH-FDR 的区别（重点理解）
=======================================
**Bonferroni**：
  p_adj = min(1, p · m)，其中 m = 测试个数
  - 控制 **FWER**（family-wise error rate，至少一个假阳的概率 ≤ α）
  - 极保守；m 大时会把几乎所有 p 拉到 1，损失功效
  - 适用场景：**确认性研究**（少量预设假设、对假阳零容忍，例如临床
    最终判读）

**Benjamini-Hochberg FDR**（更现代、更常用）：
  把 p 升序排为 p_(1) ≤ ... ≤ p_(m)，q_(i) = p_(i) · m / i，
  再做反向 cummin 保证单调，最后 cap 到 [0, 1]
  - 控制 **FDR**（错误发现比例，假阳 / 总阳的期望 ≤ α）
  - 比 Bonferroni 大方很多：m 大时也能保留若干显著结果
  - 适用场景：**探索性分析**（成千上万 ω 同时筛，例如组学差异分析、
    大规模特征筛选）

口诀：
- 想 0 个假阳？→ Bonferroni
- 假阳 / 总阳能控制在 5% 就行 → BH-FDR

输入 / 输出
==========
- ``test_id_col``：必填，每行测试的 id（写进结果 csv）
- ``p_value_col``：必填，原始 p 值列（0..1）
- ``alpha`` 默认 0.05
- 输出 ``corrected_csv`` = ``pvalues_corrected.csv``：
  [test_id, p_value, p_bonferroni, p_bh_fdr, sig_uncorrected,
   sig_bonferroni, sig_bh_fdr]
- ``summary_json``：3 种判定下的显著性计数
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - test_id_col 必填：把校正结果对回原 test 行
#   - p_value_col 必填：注意类型必须是 P_VALUE，mapper 会专门匹配
#     列名带 "p" / "pval" / "p_value" 的列
#   - static_params.alpha：默认 0.05；学术 / 临床场景可考虑 0.1（更
#     宽松，多用于探索）或 0.01（更严格）
CONTRACT = SolverContract(
    name="multiple_correction",
    capability="F04_group_difference_hypothesis_test",
    description=(
        "Bonferroni + Benjamini-Hochberg FDR correction over a column "
        "of raw p-values.  Output: csv [test_id, p_value, p_bonferroni, "
        "p_bh_fdr, sig_uncorrected, sig_bonferroni, sig_bh_fdr]."
    ),
    roles={
        "test_id_col": RoleSpec(Role.ID, "test identifier (omit → use row index)",
                                  optional=True),
        "p_value_col": RoleSpec(Role.P_VALUE, "raw p-value column"),
    },
    static_params={"alpha": 0.05},
    output_files={
        "corrected_csv": "pvalues_corrected.csv",
        "summary_json": "summary.json",
    },
    output_kind={"corrected_csv": "s", "summary_json": "s"},
)


class MultipleCorrectionSolver:
    contract = CONTRACT

    def __init__(self, alpha: float = 0.05):
        """中文：

        :param alpha: 显著性阈值。Bonferroni / BH 都基于这个 alpha
                      做接受 / 拒绝。默认 0.05 是学术通用值；探索性
                      分析（如组学初筛）有时放到 0.1。
        """
        self.alpha = alpha

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping.get("test_id_col")
        p_col = mapping["p_value_col"]
        a = self.alpha

        p = df[p_col].to_numpy(dtype=float)

        # Bonferroni：p_adj = min(1, p · m)；非常保守，控制 FWER
        # multipletests 同时返回 reject 数组和校正后 p（_, _ 是
        # alphacSidak 和 alphacBonf，这里用不到）
        rej_bonf, p_bonf, _, _ = multipletests(p, alpha=a, method="bonferroni")
        # BH-FDR：q_(i) = p_(i) · m / i，再反向 cummin 保证单调
        # 控制 false discovery rate；m 大时显著保留率比 Bonferroni 高
        rej_bh, p_bh, _, _ = multipletests(p, alpha=a, method="fdr_bh")

        ids = (df[id_col].values if id_col and id_col in df.columns
                else [f"T{i}" for i in range(len(p))])
        out = pd.DataFrame({
            "test_id":      ids,
            "p_value":      p,
            "p_bonferroni": p_bonf,
            "p_bh_fdr":     p_bh,
            "sig_uncorrected": (p < a).astype(int),
            "sig_bonferroni":  rej_bonf.astype(int),
            "sig_bh_fdr":      rej_bh.astype(int),
        })

        # carry through any other columns on request
        path = Path(output_dir) / CONTRACT.output_files["corrected_csv"]
        out.to_csv(path, index=False)

        summary = {
            "n_tests":                                int(len(p)),
            "raw_alpha":                              float(a),
            "expected_uncorrected_significant_count": int((p < a).sum()),
            "expected_bonferroni_significant_count":  int(rej_bonf.sum()),
            "expected_bh_fdr_significant_count":      int(rej_bh.sum()),
        }
        sj_path = Path(output_dir) / CONTRACT.output_files["summary_json"]
        sj_path.write_text(__import__("json").dumps(summary,
                                                     ensure_ascii=False,
                                                     indent=2),
                           encoding="utf-8")
        return {"corrected_csv": str(path),
                "summary_json": str(sj_path),
                "summary_dict": summary}


def get_solver(alpha: float = 0.05):
    return MultipleCorrectionSolver(alpha=alpha)


def selftest():
    """5 p-values, hand-checked Bonferroni and BH-FDR.

    p = [0.001, 0.008, 0.04, 0.05, 0.5]
      Bonferroni adj = min(1, p*5) = [0.005, 0.04, 0.20, 0.25, 1.0]
      BH-FDR sorted ranks 1..5: q_i = p_i * 5/i
        i=1: 0.001*5/1 = 0.005
        i=2: 0.008*5/2 = 0.02
        i=3: 0.04 *5/3 = 0.0667
        i=4: 0.05 *5/4 = 0.0625
        i=5: 0.5  *5/5 = 0.5
        cummin(reverse) → adjusted = [0.005, 0.02, 0.0625, 0.0625, 0.5]
      Significant at α=0.05:
        uncorrected: p<0.05 → ids 0,1,2  (3)
        bonferroni:  ≤0.05  → ids 0,1    (2)
        BH-FDR:      ≤0.05  → ids 0,1    (2)

    中文：用 5 个手算的 p 值做"出厂自检"。

    Fixture：p = [0.001, 0.008, 0.04, 0.05, 0.5]，m = 5

    手算逻辑（详见上面英文）：
      - 原始 α=0.05 显著：3 个（0.001, 0.008, 0.04）
      - Bonferroni 校正：min(1, p·5)，只剩 2 个 ≤ 0.05
      - BH-FDR 校正：sort + 反向 cummin，0.04 被压回 0.0625 不显著，
        0.05 也被压成 0.0625，所以也只剩 2 个

    通过判定：solver 返回的三个 _significant_count 与上面期望一致
    （5 / 3 / 2 / 2）。这同时验证了 Bonferroni 比 BH 的"严格"程度
    在 m=5 这个小规模上恰好相同（m 越大差距越明显）。
    """
    import tempfile
    df = pd.DataFrame({
        "test_id": ["T0", "T1", "T2", "T3", "T4"],
        "p_value": [0.001, 0.008, 0.04, 0.05, 0.5],
    })
    expected = {
        "n_tests": 5,
        "expected_uncorrected_significant_count": 3,
        "expected_bonferroni_significant_count":  2,
        "expected_bh_fdr_significant_count":      2,
    }
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(alpha=0.05).run(
            df=df,
            mapping=ColumnMapping({"test_id_col": "test_id",
                                     "p_value_col": "p_value"}),
            output_dir=Path(tmp),
        )
        for k, v in expected.items():
            if out["summary_dict"].get(k) != v:
                diffs.append(f"{k}: expected {v}, got "
                             f"{out['summary_dict'].get(k)}")
    return {"ok": len(diffs) == 0,
            "summary": ("Bonferroni + BH-FDR counts match hand-derived"
                        " expectations" if not diffs
                        else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["multiple_correction"]}}
