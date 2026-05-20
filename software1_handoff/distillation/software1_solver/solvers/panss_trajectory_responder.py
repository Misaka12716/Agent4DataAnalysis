"""PANSS responder solver (F10 / Q22).

Given baseline + follow-up PANSS total scores in wide format, compute
the change vs baseline at the chosen follow-up week and label patients
as responders (≥30% reduction by default).  Pure pandas, deterministic.

中文说明
========
PANSS 治疗反应评估（responder analysis）。

医学常用阈值：endpoint 比 baseline 下降 **≥ 30%** 视作 responder
（部分研究用 ≥ 20%、≥ 50%；阈值由 ``responder_threshold_pct`` 调）。

输入约定（**宽表格**）
======================
- ``id_col``        必填，单一受试者 id
- ``baseline_col``  必填，基线 PANSS 总分（一般 ``Total_0w``）
- ``endpoint_col``  必填，随访终点 PANSS 总分（一般 ``Total_12w``）

输出
====
``trajectory_csv`` = ``panss_trajectory.csv``：
  [id_col, change_0_to_12w, responder_30pct]

  - ``change_0_to_12w`` = endpoint - baseline（**有符号**，下降为负）
  - ``responder_30pct`` = 1 if (baseline - endpoint) / baseline ≥ 0.3
                          else 0
  - baseline = 0 的特殊行 → 相对变化未定义 → fillna(False) → 0

注意命名 ``change_0_to_12w`` 是历史遗留（以 12 周为典型），endpoint
不一定真是 12 周；下游解析时按位置而非名字读会更稳。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - 三个 role 都必填：宽表格场景下 baseline / endpoint 是两个固定列
#   - static_params.responder_threshold_pct：默认 30（即 30% 下降）
#     - 改成 20 → 更宽松，更多 responder
#     - 改成 50 → 更严格，常见于"显著反应"判定
CONTRACT = SolverContract(
    name="panss_trajectory_responder",
    capability="F10_time_series_followup",
    description=(
        "Compute change vs baseline + responder label (≥X% reduction) "
        "for a wide-format PANSS total trajectory."),
    roles={
        "id_col": RoleSpec(Role.ID, "patient identifier"),
        "baseline_col":  RoleSpec(Role.NUMERIC, "baseline total score"),
        "endpoint_col":  RoleSpec(Role.NUMERIC, "endpoint total score "
                                  "to compute change against"),
    },
    static_params={"responder_threshold_pct": 30},
    output_files={"trajectory_csv": "panss_trajectory.csv"},
)


class PanssTrajectoryResponderSolver:
    contract = CONTRACT

    def __init__(self, responder_threshold_pct: float = 30.0):
        """中文：

        :param responder_threshold_pct: 判定 responder 的相对下降阈值
                                        （单位 %）。默认 30 = 经典精
                                        分研究阈值；20 / 50 在不同
                                        研究里也常见。
        """
        self.responder_threshold_pct = responder_threshold_pct

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping["id_col"]
        b_col  = mapping["baseline_col"]
        e_col  = mapping["endpoint_col"]
        thresh = self.responder_threshold_pct / 100.0

        out = pd.DataFrame()
        out[id_col] = df[id_col]
        # change = endpoint - baseline，**有符号**，治疗有效则为负
        change = (df[e_col] - df[b_col])
        # name dynamically from endpoint label, e.g. "change_0_to_12w"
        out["change_0_to_12w"] = change.astype(int)
        # 相对下降幅度 = (baseline - endpoint) / baseline；
        # 用 replace(0, NA) 避免 baseline=0 行除零（结果 NA → fillna False
        # → 不算 responder，更保守）
        rel = (df[b_col] - df[e_col]) / df[b_col].replace(0, pd.NA)
        out["responder_30pct"] = (rel >= thresh).fillna(False).astype(int)

        path = Path(output_dir) / CONTRACT.output_files["trajectory_csv"]
        out.to_csv(path, index=False)

        return {"trajectory_csv": str(path),
                "responder_rate": float(out["responder_30pct"].mean())}


def get_solver(responder_threshold_pct: float = 30.0):
    return PanssTrajectoryResponderSolver(
        responder_threshold_pct=responder_threshold_pct)


def selftest():
    """3 patients with hand-derived change + responder labels.

    中文：fixture = 3 病人
      - A: 100 → 40   ΔA = -60，相对下降 60%  → responder=1
      - B:  80 → 79   ΔB = -1，  相对下降 1.25% → responder=0
      - C:  60 → 50   ΔC = -10， 相对下降 16.7% → responder=0

    通过判定：3 个病人的 change 和 responder_30pct 均与手算一致。
    """
    import tempfile
    df = pd.DataFrame({
        "PatientID": ["A", "B", "C"],
        "Total_0w":  [100, 80, 60],
        "Total_12w": [40, 79, 50],   # ΔA=-60 (-60%) / ΔB=-1 (-1.25%) / ΔC=-10 (-16.7%)
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(30.0).run(
            df=df,
            mapping=ColumnMapping({
                "id_col": "PatientID",
                "baseline_col": "Total_0w",
                "endpoint_col": "Total_12w",
            }),
            output_dir=Path(tmp),
        )
        result = pd.read_csv(out["trajectory_csv"]).set_index("PatientID")
        expected_change   = {"A": -60, "B": -1, "C": -10}
        expected_responder = {"A": 1, "B": 0, "C": 0}
        for pid in ("A", "B", "C"):
            if int(result.loc[pid, "change_0_to_12w"]) != expected_change[pid]:
                diffs.append(f"{pid}.change expected {expected_change[pid]} "
                             f"got {result.loc[pid, 'change_0_to_12w']}")
            if int(result.loc[pid, "responder_30pct"]) != expected_responder[pid]:
                diffs.append(f"{pid}.responder expected "
                             f"{expected_responder[pid]} got "
                             f"{result.loc[pid, 'responder_30pct']}")
    return {"ok": len(diffs) == 0,
            "summary": ("3 hand-derived patients match"
                        if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["panss_trajectory_responder"]}}
