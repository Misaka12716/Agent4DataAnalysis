"""Reference-range flagging solver (F13 / Q23).

Given a panel of laboratory measurements + a reference-range table,
produce per-row flags:  ``low / normal / high`` for each lab, and an
``any_abnormal`` 0/1 column.  Pure pandas, deterministic.

Reference ranges are passed as ``static_params['reference_ranges']`` —
a dict ``{lab_col: {"low": float, "high": float}}``.  In the benchmark
task this comes from ``gt/reference_range_truth.json``; in production it
would come from a Software 1 reference table.

中文说明
========
临床实验室指标 vs 参考范围逐行打旗。

输入
----
- ``id_col``      ：受试者 id（结果第一列）
- ``lab_columns`` ：要标的化验指标列名列表
- ``reference_ranges`` (静态参数)：
    ``{lab_col: {"low": float, "high": float}}``
  例如 ``{"Glucose": {"low": 3.9, "high": 6.1}}``
  没在字典里的 lab 会被静默跳过（不算异常也不算正常）。

输出
----
``flags_csv`` = ``lab_flags.csv``：
- ``id_col`` 第一列
- 每个被标的 lab 一列 ``{lab}_flag``，取值 ``low / normal / high``
- 末列 ``any_abnormal``：0/1，行内任一 lab 偏离正常即 1

判定规则（严格）
-----------------
- v < low   → "low"
- v > high  → "high"
- 否则     → "normal"
- NaN 默认 "normal"（不在异常计数里），如需"缺失也算异常"调用方
  自己 fillna 即可
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - id_col / lab_columns 必填
#   - static_params.reference_ranges：dict，必须由调用方注入
#     （contract 默认空 dict，跑起来不会报错但不会标任何列）
#   - lab_columns 里没在 reference_ranges 字典里的会被跳过 → 安全：
#     可以传一个超集
CONTRACT = SolverContract(
    name="reference_range_flag",
    capability="F13_outlier_reference_range_detection",
    description=(
        "Flag each numeric lab value against a reference range table. "
        "Output: csv with PatientID + per-lab '*_flag' columns "
        "(low/normal/high) + 'any_abnormal' 0/1."
    ),
    roles={
        "id_col": RoleSpec(Role.ID, "subject identifier"),
        "lab_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "the laboratory measurement columns to flag",
            group_hint=("typically the columns whose names appear as "
                        "keys in the reference_ranges static param"),
        ),
    },
    static_params={"reference_ranges": {}},
    output_files={"flags_csv": "lab_flags.csv"},
)


class ReferenceRangeFlagSolver:
    contract = CONTRACT

    def __init__(self, reference_ranges: Dict[str, Dict[str, float]]):
        """中文：

        :param reference_ranges: ``{lab_col: {"low": float, "high": float}}``
                                 形式的字典。**必填**：调用方负责从
                                 Software 1 的参考范围表 / GT json 里
                                 读出来后传进来；solver 自己不去查表。
        """
        self.reference_ranges = reference_ranges

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping["id_col"]
        labs = mapping["lab_columns"]

        # 防御：mapper 把 lab_columns 给的范围超集也允许；这里仅保留
        # reference_ranges 字典里有定义的列，其它静默跳过
        labs = [c for c in labs if c in self.reference_ranges]

        out = pd.DataFrame()
        out[id_col] = df[id_col]
        any_abn = pd.Series([0] * len(df), index=df.index, dtype=int)
        for lab in labs:
            ref = self.reference_ranges[lab]
            low, high = ref["low"], ref["high"]
            v = df[lab]
            # 默认 "normal"，再用 boolean mask 覆盖；
            # NaN < low / NaN > high 都是 False → NaN 保持 "normal"
            # （解释见模块顶部）
            flag = pd.Series(["normal"] * len(df), index=df.index,
                             dtype=object)
            flag[v < low] = "low"
            flag[v > high] = "high"
            out[f"{lab}_flag"] = flag
            # 行级 OR 累计 any_abnormal：任一 lab 不正常即 1
            any_abn |= (flag != "normal").astype(int)
        out["any_abnormal"] = any_abn

        path = Path(output_dir) / CONTRACT.output_files["flags_csv"]
        out.to_csv(path, index=False)
        return {"flags_csv": str(path)}


def get_solver(reference_ranges: Dict[str, Dict[str, float]]):
    return ReferenceRangeFlagSolver(reference_ranges)


def selftest():
    """3 patients × 2 labs with hand-set ranges and hand-checked flags.

    中文：fixture = 3 病人 × 2 化验
      - lab1 ∈ [2.0, 8.0]，给定 [1.0, 5.0, 10.0] → 期望 low/normal/high
      - lab2 ∈ [80, 120]，给定 [50, 100, 100]     → 期望 low/normal/normal

    通过判定：6 个旗标全部与上面手算预期一致；any_abnormal 在
    A=1（lab1 异常）、B=0（全正常）、C=1（lab1 异常）。
    """
    import tempfile
    df = pd.DataFrame({
        "PatientID": ["A", "B", "C"],
        "lab1": [1.0, 5.0, 10.0],   # low / normal / high relative to [2..8]
        "lab2": [50, 100, 100],      # low / normal / normal relative to [80..120]
    })
    refs = {"lab1": {"low": 2.0, "high": 8.0},
            "lab2": {"low": 80,   "high": 120}}
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(refs).run(
            df=df,
            mapping=ColumnMapping({"id_col": "PatientID",
                                     "lab_columns": ["lab1", "lab2"]}),
            output_dir=Path(tmp),
        )
        flags = pd.read_csv(out["flags_csv"]).set_index("PatientID")
        expected = {
            ("A", "lab1_flag"): "low", ("A", "lab2_flag"): "low",
            ("B", "lab1_flag"): "normal", ("B", "lab2_flag"): "normal",
            ("C", "lab1_flag"): "high", ("C", "lab2_flag"): "normal",
            ("A", "any_abnormal"): 1, ("B", "any_abnormal"): 0,
            ("C", "any_abnormal"): 1,
        }
        for (pid, col), exp in expected.items():
            actual = flags.loc[pid, col]
            if str(actual) != str(exp):
                diffs.append(f"{pid}.{col}: expected {exp!r}, "
                             f"got {actual!r}")
    return {"ok": len(diffs) == 0,
            "summary": ("3-patient × 2-lab fixture matches hand-derived"
                        " flags" if not diffs
                        else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["reference_range_flag"]}}
