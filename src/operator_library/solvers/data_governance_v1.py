"""DEPRECATED — V8 Phase-2 rollback snapshot.  Do not import.

Live module is ``data_governance.py``.  Pre-§2.4 implementation kept
for diff / revert.  Safe to delete after two stable benchmark cycles.

---

Data-governance atomic solvers (F01 / F13 / Q01-Q06 / Q33).

Three solvers in one module, all tiny and pure-pandas:

  - missing_summary       (Q05 缺失率, Q06 字段异常率, Q33 缺失值检查)
  - fillna_median         (Q33 数值预处理)
  - outlier_iqr_flag      (Q04 异常值检测, Q25 临床指标异常评估)

Each ships with a deterministic ``selftest()`` that uses a hand-built
fixture whose answers are obvious by inspection.

Row alignment invariant (V8 Pattern B fix)
==========================================
**Every row-shaped output CSV of these solvers includes a leftmost
``__row_id__`` column** matching the ORIGINAL DataFrame's positional
row index.  Downstream consumers (coder code, other operators) can
``df.merge(out, on='__row_id__')`` to align rows safely, even after
the coder filters / reorders rows.  Aggregate outputs (``missing_summary``,
which is one row per column not per row of input) are exempt.

中文说明
========
数据治理三件套（纯 pandas / numpy，无外部依赖）：

1. ``missing_summary``：逐列统计缺失数 / 缺失率 / 唯一值数 / dtype。
   - 输入：任意 DataFrame，无角色映射要求
   - 输出 ``summary_csv`` = ``missing_summary.csv``
     [column, dtype, n_missing, missing_rate, n_unique]

2. ``fillna_median``：用 **列中位数** 填补数值列的 NaN，非数值列原样
   pass-through。
   - 输入：可选 ``numeric_columns``，不传就自动选所有数值列
   - 输出 ``filled_csv`` = ``filled.csv``
   - 选 median 不选 mean 的理由：极端值存在时 median 更稳

3. ``outlier_iqr_flag``：Tukey 围栏 [Q1-k·IQR, Q3+k·IQR] 标记异常。
   - 输入：``numeric_columns``（必填）+ 可选 ``id_col``
   - 静态参数：``k`` 默认 1.5（经典 Tukey 阈值；2.0 = 更宽松）
   - 输出 ``flags_csv`` = ``iqr_outlier_flags.csv``
     [id?, {col}_outlier×N, any_outlier]
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# ---------------------------------------------------------------------------
# Solver 1: missing_summary  (Q05 / Q06 / Q33)
# ---------------------------------------------------------------------------
# Contract 说明：
#   - roles 为空 → mapper 不需要做任何列名解析，solver 直接吃整个 DataFrame
#   - 没有 static_params，行为完全确定
#   - 输出仅一个 csv：缺失统计表
MISSING_SUMMARY_CONTRACT = SolverContract(
    name="missing_summary",
    capability="F01_data_governance_cleaning",
    description=(
        "Per-column missing-value summary: missing count, missing rate, "
        "n_unique, dtype.  Output: csv [column, dtype, n_missing, "
        "missing_rate, n_unique]."
    ),
    roles={},  # operates on the entire DataFrame
    output_files={"summary_csv": "missing_summary.csv"},
)


class MissingSummarySolver:
    contract = MISSING_SUMMARY_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        n = len(df)
        rows = []
        # 逐列扫描：n_missing 用 isna().sum()，n_unique 用 nunique(dropna=True)
        # 这样"全 NaN 列"的 n_unique=0，便于下游识别"无信息列"
        for c in df.columns:
            s = df[c]
            nm = int(s.isna().sum())
            rows.append({
                "column":       str(c),
                "dtype":        str(s.dtype),
                "n_missing":    nm,
                "missing_rate": round(nm / n, 6) if n else 0.0,
                "n_unique":     int(s.nunique(dropna=True)),
            })
        out = pd.DataFrame(rows)
        path = Path(output_dir) / MISSING_SUMMARY_CONTRACT.output_files["summary_csv"]
        out.to_csv(path, index=False)
        return {"summary_csv": str(path),
                "summary_df": out,
                "total_missing_cells": int(out["n_missing"].sum()),
                "n_rows": n}


# ---------------------------------------------------------------------------
# Solver 2: fillna_median  (Q33)
# ---------------------------------------------------------------------------
# Contract 说明：
#   - numeric_columns 是 NUMERIC_LIST 类型且 optional：
#       * 不传 → solver 自己用 is_numeric_dtype 选所有数值列
#       * 传了 → 严格只填这些列，其它列即使是数值也不动
#   - 没有 static_params：行为唯一（always median, ddof 不参与）
FILLNA_MEDIAN_CONTRACT = SolverContract(
    name="fillna_median",
    capability="F01_data_governance_cleaning",
    description=(
        "Fill missing values in numeric columns with the column median. "
        "Non-numeric columns are passed through unchanged.  Deterministic."
    ),
    roles={
        "numeric_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "the numeric columns to fill (others pass through)",
            optional=True,
        ),
    },
    output_files={"filled_csv": "filled.csv"},
)


class FillNaMedianSolver:
    contract = FILLNA_MEDIAN_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        cols = mapping.get("numeric_columns")
        if not cols:
            cols = [c for c in df.columns
                    if pd.api.types.is_numeric_dtype(df[c])]
        out = df.copy()
        medians = {}
        # 严格用 pandas Series.median()（内部走 nanmedian，不受 NaN 影响）
        # 选 median 而非 mean：少量极端值存在时更稳；非数值列原样跳过
        for c in cols:
            m = float(out[c].median())
            medians[c] = m
            out[c] = out[c].fillna(m)
        # V8 Pattern B fix: prepend __row_id__ (positional index of the
        # ORIGINAL df) so the coder can merge the filled output back
        # against the raw table without ambiguity.
        out.insert(0, "__row_id__", range(len(df)))
        path = Path(output_dir) / FILLNA_MEDIAN_CONTRACT.output_files["filled_csv"]
        out.to_csv(path, index=False)
        return {"filled_csv": str(path),
                "medians": medians,
                "n_filled_cells": int(df[cols].isna().sum().sum())}


# ---------------------------------------------------------------------------
# Solver 3: outlier_iqr_flag  (Q04 / Q25)
# ---------------------------------------------------------------------------
# Contract 说明：
#   - id_col              optional：纯数值表也能跑，但有 id 时会带在输出里
#   - numeric_columns     必填：要标的列（其它列即使数值也不动）
#   - static_params.k     默认 1.5（经典 Tukey）；2.0 = 更宽松，3.0 = 极端值
OUTLIER_IQR_CONTRACT = SolverContract(
    name="outlier_iqr_flag",
    capability="F13_outlier_reference_range_detection",
    description=(
        "Flag values outside [Q1 - k*IQR, Q3 + k*IQR] (Tukey fences) "
        "for each numeric column.  Output: csv with id col + per-column "
        "0/1 outlier flag + any_outlier."
    ),
    roles={
        "id_col":           RoleSpec(Role.ID, "subject identifier",
                                      optional=True),
        "numeric_columns":  RoleSpec(Role.NUMERIC_LIST,
                                      "numeric columns to flag"),
    },
    static_params={"k": 1.5},
    output_files={"flags_csv": "iqr_outlier_flags.csv"},
)


class OutlierIqrFlagSolver:
    contract = OUTLIER_IQR_CONTRACT

    def __init__(self, k: float = 1.5):
        """中文：

        :param k: Tukey 围栏的乘数。1.5 是教科书默认（覆盖正态约 99.3%），
                  2.0 倾向"只标极端值"，3.0 几乎只剩明显错误。
        """
        self.k = k

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping.get("id_col")
        cols = mapping.get("numeric_columns") or [
            c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
        ]
        out = pd.DataFrame()
        # V8 Pattern B fix: __row_id__ is ALWAYS the leftmost column so
        # downstream code can ``df.merge(flags, on='__row_id__')``
        # without losing alignment when the coder filters / reorders.
        out["__row_id__"] = range(len(df))
        if id_col and id_col in df.columns:
            out[id_col] = df[id_col].values
        any_out = pd.Series(0, index=df.index, dtype=int)
        bounds: Dict[str, Dict[str, float]] = {}
        for c in cols:
            v = df[c].astype(float)
            # 用 nanpercentile（NaN 不参与分位数估计），避免一个 NaN 把
            # Q1/Q3 拖偏
            q1 = float(np.nanpercentile(v, 25))
            q3 = float(np.nanpercentile(v, 75))
            iqr = q3 - q1
            # Tukey 围栏：[Q1 - k·IQR, Q3 + k·IQR]，k 默认 1.5
            low, high = q1 - self.k * iqr, q3 + self.k * iqr
            bounds[c] = {"q1": q1, "q3": q3, "iqr": iqr,
                         "low": low, "high": high}
            # NaN → 不算 outlier（fillna(False)），避免缺失污染统计
            flag = ((v < low) | (v > high)).fillna(False).astype(int)
            out[f"{c}_outlier"] = flag.values
            # any_outlier 是按行做 OR：任一列越界，行就是 outlier
            any_out |= flag.values
        out["any_outlier"] = any_out.values
        path = Path(output_dir) / OUTLIER_IQR_CONTRACT.output_files["flags_csv"]
        out.to_csv(path, index=False)
        return {"flags_csv": str(path),
                "bounds": bounds,
                "n_outlier_rows": int(any_out.sum())}


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------
def get_missing_summary_solver(): return MissingSummarySolver()
def get_fillna_median_solver(): return FillNaMedianSolver()
def get_outlier_iqr_solver(k: float = 1.5): return OutlierIqrFlagSolver(k=k)


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """Hand-built fixture with obvious ground truth.

    中文：3 个 sub-solver 的"出厂自检"。

    Fixture：
      - missing_summary / fillna_median：5 行 × 3 列，col_a 含 1 个 NaN，
        col_b 含 2 个 NaN，col_c 全字符串。所有期望值都能口算。
      - outlier_iqr_flag：8 行 lab=[10..16, 100]，IQR ≈ 3，1.5×IQR
        围栏会唯一标出最后一行 100。

    通过判定：
      - missing_summary：n_missing 与肉眼一致，col_b 缺失率 = 0.4
      - fillna_median：col_a NaN 填 3.0（{1,2,4,5} 的 median），
                       col_b NaN 填 30.0；非数值列 col_c 原样
      - outlier_iqr_flag：仅最后一行 (lab=100) 被标记
    """
    import tempfile

    # 5 rows; col_a has 1 NaN, col_b has 2 NaN, col_c is full
    csv = io.StringIO(
        "id,col_a,col_b,col_c\n"
        "P1,1,10,X\n"
        "P2,2,,X\n"
        "P3,,30,Y\n"
        "P4,4,,Y\n"
        "P5,5,50,Z\n"
    )
    df = pd.read_csv(csv)

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # 1. missing_summary --------------------------------------------------
        ms = get_missing_summary_solver()
        out = ms.run(df=df, mapping=ColumnMapping({}), output_dir=tmp)
        sdf = out["summary_df"].set_index("column")
        if int(sdf.loc["col_a", "n_missing"]) != 1:
            diffs.append("missing_summary: col_a expected 1 NaN")
        if int(sdf.loc["col_b", "n_missing"]) != 2:
            diffs.append("missing_summary: col_b expected 2 NaN")
        if int(sdf.loc["col_c", "n_missing"]) != 0:
            diffs.append("missing_summary: col_c expected 0 NaN")
        if abs(float(sdf.loc["col_b", "missing_rate"]) - 0.4) > 1e-9:
            diffs.append("missing_summary: col_b missing_rate != 0.4")

        # 2. fillna_median ----------------------------------------------------
        fm = get_fillna_median_solver()
        out2 = fm.run(df=df, mapping=ColumnMapping({}), output_dir=tmp)
        filled = pd.read_csv(out2["filled_csv"])
        # V8 Pattern B fix: leftmost column must be __row_id__ matching
        # the positional index of the original df.
        if list(filled.columns)[0] != "__row_id__":
            diffs.append("fillna_median: leftmost column must be __row_id__")
        if filled["__row_id__"].tolist() != list(range(len(df))):
            diffs.append("fillna_median: __row_id__ should be 0..N-1 "
                         "matching original df row positions")
        # col_a values [1,2,_,4,5] median=3.0 → fill index 2
        if not np.isclose(filled.loc[2, "col_a"], 3.0):
            diffs.append(f"fillna_median: col_a NaN should be 3.0, "
                         f"got {filled.loc[2, 'col_a']}")
        # col_b values [10,_,30,_,50] median=30 (of {10,30,50}) → fill 1,3
        if not np.isclose(filled.loc[1, "col_b"], 30.0):
            diffs.append(f"fillna_median: col_b NaN should be 30.0, "
                         f"got {filled.loc[1, 'col_b']}")
        if filled["col_c"].tolist() != ["X", "X", "Y", "Y", "Z"]:
            diffs.append("fillna_median: col_c should be unchanged")

        # 3. outlier_iqr_flag -------------------------------------------------
        # build a column where 100 is an obvious outlier
        df_out = pd.DataFrame({
            "id":   [f"P{i}" for i in range(8)],
            "lab":  [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 100.0],
        })
        oi = get_outlier_iqr_solver(k=1.5)
        out3 = oi.run(df=df_out,
                       mapping=ColumnMapping({"id_col": "id",
                                              "numeric_columns": ["lab"]}),
                       output_dir=tmp)
        flags = pd.read_csv(out3["flags_csv"])
        # V8 Pattern B fix: leftmost column must be __row_id__ matching
        # the positional row index of the original df.
        if list(flags.columns)[0] != "__row_id__":
            diffs.append("outlier_iqr: leftmost column must be __row_id__")
        if flags["__row_id__"].tolist() != list(range(len(df_out))):
            diffs.append("outlier_iqr: __row_id__ should be 0..N-1 "
                         "matching original df row positions")
        # row 7 (lab=100) should be the only outlier
        if flags["lab_outlier"].tolist() != [0, 0, 0, 0, 0, 0, 0, 1]:
            diffs.append(f"outlier_iqr: expected only row 7 flagged, "
                         f"got {flags['lab_outlier'].tolist()}")
        if int(flags["any_outlier"].sum()) != 1:
            diffs.append("outlier_iqr: any_outlier sum should be 1")

    return {
        "ok":      len(diffs) == 0,
        "summary": ("3/3 sub-solvers pass" if not diffs
                    else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["missing_summary", "fillna_median",
                                "outlier_iqr_flag"]},
    }
