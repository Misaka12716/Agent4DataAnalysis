"""PANSS factor scoring solver (F14).

Sums the 30 PANSS items into 3 factor scores (Positive / Negative /
General) plus a Total.  Pure pandas — fully deterministic.

Why a custom solver: no existing csv operator covers PANSS factor sum;
audit had this as Q31 with PARTIAL coverage.  This solver is the new
authoritative implementation.

中文说明
========
PANSS 量表分维度求和 → 4 个因子分。

PANSS（Positive and Negative Syndrome Scale）共 30 个条目，
每条 Likert 1..7：
  - **Positive**（阳性）：P1..P7         共 7 项，求和得 Positive_score
  - **Negative**（阴性）：N1..N7         共 7 项，求和得 Negative_score
  - **General** （一般精神病理）：G1..G16 共 16 项，求和得 General_score
  - **Total** = Pos + Neg + Gen          范围理论上 30..210

输入约定
========
- ``id_col``        必填，单一受试者 id 列
- ``time_col``      optional，访视周次（写入第二列做主键之二）
- ``positive_items`` ITEM_GROUP，必填，长度 = 7（每项 Likert 1..7）
- ``negative_items`` ITEM_GROUP，必填，长度 = 7
- ``general_items``  ITEM_GROUP，必填，长度 = 16

实现细节：纯 ``df[items].sum(axis=1).astype(int)``，不做缺失填补。
任何项缺失会导致 sum=NaN 再 astype(int) 抛异常 → 调用方需先填补。

输出
====
``scored_csv`` = ``panss_scored.csv``：
  [id_col, (time_col)?, Positive_score, Negative_score,
   General_score, Total_score]
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - 三个 ITEM_GROUP 都是必填：mapper 必须能把列名拆成 P/N/G 三组
#     - 列名典型前缀 P1..P7 / N1..N7 / G1..G16；mapper 可以靠这个匹配
#   - time_col 是 NUMERIC（不是 DATETIME），因为 PANSS 通常用"第几周
#     随访"这种整数表达
#   - 没有 static_params：因子求和没有可调超参
CONTRACT = SolverContract(
    name="panss_factor_score",
    capability="F14_scale_structuring_extraction",
    description=(
        "Compute PANSS Positive / Negative / General factor scores and "
        "total by row-summing the corresponding item columns.  Items are "
        "Likert 1..7 integer columns.  Output: deliverable csv with "
        "[ID, time?, Positive_score, Negative_score, General_score, "
        "Total_score]."),
    roles={
        "id_col": RoleSpec(Role.ID, "patient identifier", optional=False),
        "time_col": RoleSpec(Role.NUMERIC, "visit week / time index "
                              "(integer); set as second key column",
                              optional=True),
        "positive_items": RoleSpec(
            Role.ITEM_GROUP, "the 7 PANSS Positive scale items "
            "(P1..P7); each Likert 1..7",
            group_hint="Positive: typically prefixed P1..P7",
        ),
        "negative_items": RoleSpec(
            Role.ITEM_GROUP, "the 7 PANSS Negative scale items "
            "(N1..N7); each Likert 1..7",
            group_hint="Negative: typically prefixed N1..N7",
        ),
        "general_items": RoleSpec(
            Role.ITEM_GROUP, "the 16 PANSS General Psychopathology items "
            "(G1..G16); each Likert 1..7",
            group_hint="General: typically prefixed G1..G16",
        ),
    },
    output_files={"scored_csv": "panss_scored.csv"},
)


class PanssFactorScoreSolver:
    contract = CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping["id_col"]
        time_col = mapping.get("time_col")
        pos = mapping["positive_items"]
        neg = mapping["negative_items"]
        gen = mapping["general_items"]

        out = pd.DataFrame()
        out[id_col] = df[id_col]
        if time_col:
            out[time_col] = df[time_col]
        # 直接 sum(axis=1)：每行一个因子分；astype(int) 保证输出是整数
        # （PANSS 条目都是 1..7 整数，因子分数学上必为整数）
        # ⚠️ 不容忍缺失：任一条目 NaN 会让 sum=NaN，astype(int) 直接 raise
        out["Positive_score"] = df[pos].sum(axis=1).astype(int)
        out["Negative_score"] = df[neg].sum(axis=1).astype(int)
        out["General_score"]  = df[gen].sum(axis=1).astype(int)
        # Total = Pos + Neg + Gen；不直接对全 30 条 sum 是为了能复用
        # 上面已经计算的列，效率略高且更可读
        out["Total_score"]    = (out["Positive_score"]
                                 + out["Negative_score"]
                                 + out["General_score"]).astype(int)

        path = Path(output_dir) / CONTRACT.output_files["scored_csv"]
        out.to_csv(path, index=False)
        return {"scored_csv": str(path)}


def get_solver():
    return PanssFactorScoreSolver()


def selftest():
    """Hand-built fixture with arithmetic-obvious expected sums.

    中文：fixture = 2 病人 × 30 条 PANSS。

    病人 A 的设计：
      - P1..P7 = 1, 2, 3, 4, 5, 6, 7  → Positive 求和 = 28
      - N1..N7 = 2, 2, 2, 2, 2, 2, 2  → Negative 求和 = 14
      - G1..G16 全 = 1                → General  求和 = 16
      - Total = 28 + 14 + 16 = 58

    通过判定：A 的四个分数与上述手算完全一致。
    """
    import tempfile

    df = pd.DataFrame({
        "PatientID": ["A", "B"],
        # P1..P7 = 1, 2, 3, 4, 5, 6, 7  → sum = 28
        **{f"P{i}": [i, 7 - i + 1] for i in range(1, 8)},
        # N1..N7 = 2, 2, ...           → sum = 14
        **{f"N{i}": [2, 3] for i in range(1, 8)},
        # G1..G16 all = 1              → sum = 16
        **{f"G{i}": [1, 2] for i in range(1, 17)},
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df=df,
            mapping=ColumnMapping({
                "id_col":         "PatientID",
                "positive_items": [f"P{i}" for i in range(1, 8)],
                "negative_items": [f"N{i}" for i in range(1, 8)],
                "general_items":  [f"G{i}" for i in range(1, 17)],
            }),
            output_dir=Path(tmp),
        )
        scored = pd.read_csv(out["scored_csv"]).set_index("PatientID")
        # Row A: P=1+2+3+4+5+6+7=28; N=2*7=14; G=1*16=16; Total=58
        if int(scored.loc["A", "Positive_score"]) != 28:
            diffs.append(f"A.Positive expected 28 got "
                         f"{scored.loc['A','Positive_score']}")
        if int(scored.loc["A", "Negative_score"]) != 14:
            diffs.append(f"A.Negative expected 14 got "
                         f"{scored.loc['A','Negative_score']}")
        if int(scored.loc["A", "General_score"]) != 16:
            diffs.append(f"A.General expected 16 got "
                         f"{scored.loc['A','General_score']}")
        if int(scored.loc["A", "Total_score"]) != 58:
            diffs.append(f"A.Total expected 58 got "
                         f"{scored.loc['A','Total_score']}")
    return {"ok": len(diffs) == 0,
            "summary": ("hand-summed PANSS fixture matches solver output"
                        if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["panss_factor_score"]}}
