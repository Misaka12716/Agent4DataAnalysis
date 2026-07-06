"""Time-series feature extraction (F09 / Q17 时序特征).

Convert long-format longitudinal data
   [id_col, time_col, value_col_1, value_col_2, ...]
into wide patient-level features, one row per id, columns:
   {value}_n, {value}_first, {value}_last, {value}_min, {value}_max,
   {value}_mean, {value}_std, {value}_slope (OLS over time),
   {value}_auc  (trapezoid AUC over time)

These features are exactly the kind a downstream classifier (RF/SVM/LR)
expects, so this solver acts as the bridge between time-series tasks
and the F06 supervised classification solvers.

中文说明
========
长表格随访数据 → 每病人一行的宽特征。

输入约定（**长表格**）
======================
- 一行 = 一次随访记录
- ``id_col``         必填，病人 id（一个病人可以有多行）
- ``time_col``       必填，数值时间索引（visit week, day, ...；越大越晚）
- ``value_columns``  必填，数值测量列列表

每个 value 列会被聚合成 9 个特征：
  - ``{c}_n``     非空记录数
  - ``{c}_first`` 第一次（按时间排序）的非空值
  - ``{c}_last``  最后一次
  - ``{c}_min`` / ``{c}_max`` / ``{c}_mean``
  - ``{c}_std``   样本标准差 ddof=1（n<=1 → 0）
  - ``{c}_slope`` 对时间做 OLS 回归得到的斜率（< 2 个点或时间无变化 → NaN）
  - ``{c}_auc``   对时间做梯形积分（trapezoid）的曲线下面积

输出
====
``features_csv`` = ``ts_features.csv``：宽表，一行一病人，
列数 = 1 + 9·len(value_columns)。可直接喂给下游 LR / 树 / SVM solver。

设计意图：作为 F06 监督分类 solver 的"上游桥梁"。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - 三个 role 都是必填：长表格场景下 id / time / value 缺一不可
#   - 没有 static_params：聚合行为固定（OLS 1 阶 + 梯形 AUC）
CONTRACT = SolverContract(
    name="time_series_features",
    capability="F09_dimensionality_reduction_features",
    description=(
        "Aggregate longitudinal observations into per-id features: n, "
        "first, last, min, max, mean, std, OLS slope vs time, "
        "trapezoid AUC.  Output: wide csv keyed by id."),
    roles={
        "id_col":         RoleSpec(Role.ID,           "subject identifier"),
        "time_col":       RoleSpec(Role.NUMERIC,
                                    "numeric time index (visit week, day, …)"),
        "value_columns":  RoleSpec(Role.NUMERIC_LIST,
                                    "numeric measurement columns to summarise"),
    },
    output_files={"features_csv": "ts_features.csv"},
    output_kind={"features_csv": "t"},
)


def _slope(t: np.ndarray, v: np.ndarray) -> float:
    """OLS slope; nan if fewer than 2 distinct time points.

    中文：np.polyfit(t, v, 1) 解一元线性回归 v = a·t + b，coef[0] = a 即斜率。
    需要至少 2 个不同时间点；t.std()=0 时回归矩阵奇异 → 返回 NaN。
    """
    m = ~np.isnan(v)
    t, v = t[m], v[m]
    if len(t) < 2 or t.std() == 0:
        return float("nan")
    coef = np.polyfit(t, v, 1)
    return float(coef[0])


def _auc(t: np.ndarray, v: np.ndarray) -> float:
    """中文：梯形 AUC。先 dropna，再按时间升序，最后 np.trapz。
    < 2 个点 → 没有梯形 → NaN。
    """
    m = ~np.isnan(v)
    t, v = t[m], v[m]
    if len(t) < 2:
        return float("nan")
    # 必须按时间升序，否则 trapz 会产生符号错乱（负面积）
    order = np.argsort(t)
    return float(np.trapz(v[order], t[order]))


class TimeSeriesFeaturesSolver:
    contract = CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping["id_col"]
        time_col = mapping["time_col"]
        cols: List[str] = list(mapping["value_columns"])

        rows = []
        # 先按 (id, time) 升序排，后续 first / last 才有"按时间最早 / 最晚"
        # 的语义；按 id groupby 出每个病人的子表
        for pid, sub in df.sort_values([id_col, time_col]).groupby(id_col):
            t = sub[time_col].astype(float).to_numpy()
            row: Dict[str, Any] = {id_col: pid}
            for c in cols:
                v = sub[c].astype(float).to_numpy()
                # vm = 该列非空值（有序）；vm[0]/vm[-1] 才是真"first/last 非空"
                vm = v[~np.isnan(v)]
                row[f"{c}_n"]     = int(len(vm))
                row[f"{c}_first"] = float(vm[0])  if len(vm) else float("nan")
                row[f"{c}_last"]  = float(vm[-1]) if len(vm) else float("nan")
                row[f"{c}_min"]   = float(vm.min()) if len(vm) else float("nan")
                row[f"{c}_max"]   = float(vm.max()) if len(vm) else float("nan")
                row[f"{c}_mean"]  = float(vm.mean()) if len(vm) else float("nan")
                # 样本 std (ddof=1)；只有 1 个观测时 std 数学未定义，硬编 0
                row[f"{c}_std"]   = float(vm.std(ddof=1)) if len(vm) > 1 else 0.0
                # slope / auc 需要原始的 (t, v) 配对（含 NaN，函数内自己 dropna）
                row[f"{c}_slope"] = _slope(t, v)
                row[f"{c}_auc"]   = _auc(t, v)
            rows.append(row)

        out = pd.DataFrame(rows)
        path = Path(output_dir) / CONTRACT.output_files["features_csv"]
        out.to_csv(path, index=False)
        return {"features_csv": str(path),
                "features_df":  out,
                "n_subjects":   int(out.shape[0]),
                "n_features_per_subject": int(out.shape[1] - 1)}


def get_solver():
    return TimeSeriesFeaturesSolver()


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """Construct 2 patients with linear & quadratic trajectories;
    verify slope, mean, AUC against analytic answers.

    中文：fixture = 2 病人，全部用 **解析解可手算** 的轨迹。

    病人 A：t=[0..4]，v = 2 + 3·t = [2, 5, 8, 11, 14]
      - slope = 3.0      （线性回归恰好 = 系数）
      - mean  = 8.0      （算术平均）
      - auc   = trapz([2,5,8,11,14], [0..4]) = 32.0
      - n=5, first=2.0, last=14.0

    病人 B：t=[0,2,4]，v = 10 - 2·t = [10, 6, 2]
      - slope = -2.0
      - mean  = 6.0

    通过判定：上述所有数值与公式精确一致（误差 < 1e-9）。
    """
    import tempfile

    # Patient A: t=[0,1,2,3,4]; v_lin = 2 + 3*t  → slope=3, mean=2+3*2=8
    #            AUC of straight line: trapz([2,5,8,11,14], [0..4]) = 32
    # Patient B: t=[0,2,4]; v_lin = 10 - 2*t   → slope=-2, mean=10-2*2=6
    rows = []
    for t, v in zip([0, 1, 2, 3, 4], [2.0, 5.0, 8.0, 11.0, 14.0]):
        rows.append({"PatientID": "A", "VisitWeek": t, "score": v})
    for t, v in zip([0, 2, 4], [10.0, 6.0, 2.0]):
        rows.append({"PatientID": "B", "VisitWeek": t, "score": v})
    df = pd.DataFrame(rows)

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df=df,
            mapping=ColumnMapping({
                "id_col": "PatientID",
                "time_col": "VisitWeek",
                "value_columns": ["score"],
            }),
            output_dir=Path(tmp),
        )
        f = out["features_df"].set_index("PatientID")
        # A
        if abs(f.loc["A", "score_slope"] - 3.0) > 1e-9:
            diffs.append(f"A.slope expected 3.0, got {f.loc['A', 'score_slope']}")
        if abs(f.loc["A", "score_mean"] - 8.0) > 1e-9:
            diffs.append(f"A.mean expected 8.0, got {f.loc['A', 'score_mean']}")
        if abs(f.loc["A", "score_auc"] - 32.0) > 1e-9:
            diffs.append(f"A.auc expected 32.0, got {f.loc['A', 'score_auc']}")
        if int(f.loc["A", "score_n"]) != 5:
            diffs.append("A.n expected 5")
        if abs(f.loc["A", "score_first"] - 2.0) > 1e-9 or \
           abs(f.loc["A", "score_last"] - 14.0) > 1e-9:
            diffs.append("A first/last mismatch")
        # B
        if abs(f.loc["B", "score_slope"] - (-2.0)) > 1e-9:
            diffs.append(f"B.slope expected -2.0, got {f.loc['B', 'score_slope']}")
        if abs(f.loc["B", "score_mean"] - 6.0) > 1e-9:
            diffs.append(f"B.mean expected 6.0, got {f.loc['B', 'score_mean']}")
    return {"ok": len(diffs) == 0,
            "summary": ("2-patient hand-derived slope/mean/AUC match"
                        if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["time_series_features"]}}
