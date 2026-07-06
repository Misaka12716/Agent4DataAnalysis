"""Data consistency + accuracy checks (F01 / Q01-Q03).

Two solvers handling the dimensions Software 1 explicitly demands but
that nothing else covers cleanly:

  - consistency_check: primary-key uniqueness, regex format, value-range
  - accuracy_check   : cross-field constraints (date order, BMI ≈ w/h²,
                       computed-vs-reported equality, …)

Both produce a *long-format* `issues_csv` so downstream cleanup or
LLM-driven repair can iterate over individual problems.

中文说明
========
数据质量两件套，对应 Software 1 的"一致性 / 准确性"两个维度，
其它 solver 都不覆盖。

1. ``consistency_check``：单列规则
   - 主键唯一（``id_col``）
   - 字符串正则匹配（``regex_rules``：``{col: pattern}``）
   - 数值范围（``range_rules``：``{col: (low, high)}``，None=不限）
   - 类别白名单（``allowed_values``：``{col: [v1, v2, ...]}``）
   输出：``consistency_issues.csv``（长表，每行 1 个 issue）+
         ``consistency_summary.json``（计数 / by_issue_type）

2. ``accuracy_check``：行级跨字段约束
   - ``constraints``：list[{name, columns, predicate, message}]
     每个 predicate 是 ``Callable[[pd.Series], bool]``，True=该行 OK
   - 适合 BMI ≈ w/h²、入院日期 ≤ 出院日期、computed=reported 等
   输出：``accuracy_issues.csv`` + ``accuracy_summary.json``

输入约定
========
- 长表 issues_csv 设计成 *one issue per row*，下游 LLM 修复 / 人工
  审核可以逐行迭代
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# ---------------------------------------------------------------------------
# consistency_check
# ---------------------------------------------------------------------------
# Contract 说明：
#   - id_col            optional：主键唯一性检查；不传就跳过
#   - regex_rules       optional：{col: pattern}，str fullmatch
#   - range_rules       optional：{col: (low, high)}，None 端表示不限
#   - allowed_values    optional：{col: [v1, ...]}，类别白名单
#   - 全是 PARAMS 角色 → mapper 不会自动赋值，必须由调用者直接传 dict
CONSISTENCY_CONTRACT = SolverContract(
    name="consistency_check",
    capability="F01_data_governance_cleaning",
    description=(
        "Primary-key uniqueness + per-column rules: regex format and "
        "value range.  Output: issues_csv [row_index, column, value, "
        "issue_type, message] + summary."),
    roles={
        "id_col":          RoleSpec(Role.ID, "primary key column",
                                     optional=True),
        "regex_rules":     RoleSpec(Role.PARAMS,
                                     "dict {col: regex} for str format check",
                                     optional=True),
        "range_rules":     RoleSpec(Role.PARAMS,
                                     "dict {col: (low, high)} numeric range",
                                     optional=True),
        "allowed_values":  RoleSpec(Role.PARAMS,
                                     "dict {col: [v1, v2, ...]} categorical",
                                     optional=True),
    },
    output_files={"issues_csv":  "consistency_issues.csv",
                  "summary_json": "consistency_summary.json"},
    output_kind={"issues_csv": "s", "summary_json": "s"},
)


class ConsistencyCheckSolver:
    contract = CONSISTENCY_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        issues: List[Dict[str, Any]] = []

        # --- primary key uniqueness ---
        # keep=False：把所有重复的拷贝都标出来（不只第一个），下游
        # 才能看到 "P001 出现 2 次" 而不是 "1 个 duplicate"
        id_col = mapping.get("id_col")
        if id_col and id_col in df.columns:
            dup_mask = df[id_col].duplicated(keep=False)
            for idx in df.index[dup_mask]:
                issues.append({
                    "row_index": int(idx),
                    "column":    id_col,
                    "value":     str(df.at[idx, id_col]),
                    "issue_type": "duplicate_id",
                    "message":   f"primary-key value duplicated",
                })

        # --- regex rules ---
        # 用 fullmatch（不是 search）：要求整段字符串都匹配，避免
        # "P001abc" 因为前缀 "P001" 而漏检。NaN 跳过不算 mismatch
        regex_rules = mapping.get("regex_rules") or {}
        for col, pattern in regex_rules.items():
            if col not in df.columns:
                continue
            cre = re.compile(pattern)
            for idx, val in df[col].items():
                if pd.isna(val):
                    continue
                if not cre.fullmatch(str(val)):
                    issues.append({
                        "row_index": int(idx),
                        "column":    col,
                        "value":     str(val),
                        "issue_type": "regex_mismatch",
                        "message":   f"value does not match {pattern!r}",
                    })

        # --- range rules ---
        # 数值范围检查：(low, high) 任一端可以为 None（表示不限）
        # - 如果该 cell 转 float 失败 → 单独记 non_numeric 而不是抛
        range_rules = mapping.get("range_rules") or {}
        for col, bounds in range_rules.items():
            if col not in df.columns:
                continue
            low, high = bounds
            for idx, val in df[col].items():
                if pd.isna(val):
                    continue
                try:
                    f = float(val)
                except (TypeError, ValueError):
                    issues.append({
                        "row_index": int(idx),
                        "column":    col,
                        "value":     str(val),
                        "issue_type": "non_numeric",
                        "message":   "expected a number",
                    })
                    continue
                if (low is not None and f < low) or \
                   (high is not None and f > high):
                    issues.append({
                        "row_index": int(idx),
                        "column":    col,
                        "value":     str(val),
                        "issue_type": "out_of_range",
                        "message":   f"value {f} outside [{low}, {high}]",
                    })

        # --- allowed-values rules ---
        allowed = mapping.get("allowed_values") or {}
        for col, values in allowed.items():
            if col not in df.columns:
                continue
            allowed_set = set(values)
            for idx, val in df[col].items():
                if pd.isna(val):
                    continue
                if val not in allowed_set:
                    issues.append({
                        "row_index": int(idx),
                        "column":    col,
                        "value":     str(val),
                        "issue_type": "value_not_allowed",
                        "message":   f"value not in {sorted(allowed_set)}",
                    })

        out = pd.DataFrame(issues, columns=["row_index", "column", "value",
                                              "issue_type", "message"])
        ip = Path(output_dir) / CONSISTENCY_CONTRACT.output_files["issues_csv"]
        out.to_csv(ip, index=False)

        summary = {
            "n_rows":         int(len(df)),
            "n_issues":       int(len(out)),
            "by_issue_type":  out["issue_type"].value_counts().to_dict()
                              if not out.empty else {},
            "is_clean":       bool(out.empty),
        }
        sp = Path(output_dir) / CONSISTENCY_CONTRACT.output_files["summary_json"]
        sp.write_text(__import__("json").dumps(summary, ensure_ascii=False,
                                                  indent=2),
                       encoding="utf-8")
        return {"issues_csv":   str(ip),
                "summary_json": str(sp),
                "summary_dict": summary,
                "issues_df":    out}


# ---------------------------------------------------------------------------
# accuracy_check
# ---------------------------------------------------------------------------
AccuracyConstraint = Dict[str, Any]
# {"name": str,
#  "columns": [...],     # columns this constraint touches
#  "predicate": Callable[[pd.Series], bool],   # row → bool, True = ok
#  "message": str}

# Contract 说明：
#   - constraints 是 PARAMS（必填）：list[dict]，每个 dict 描述一条
#     行级跨字段约束。predicate 是 Python 函数对象，所以这个 solver
#     必须由本进程的 Python 调用方传入，不能跨进程序列化
ACCURACY_CONTRACT = SolverContract(
    name="accuracy_check",
    capability="F01_data_governance_cleaning",
    description=(
        "Apply user-supplied cross-field constraints, each a row-level "
        "predicate.  Returns issues_csv + summary."),
    roles={
        "constraints": RoleSpec(
            Role.PARAMS,
            "list of {name, columns, predicate, message} dicts",
            optional=False,
        ),
    },
    output_files={"issues_csv":  "accuracy_issues.csv",
                  "summary_json": "accuracy_summary.json"},
    output_kind={"issues_csv": "s", "summary_json": "s"},
)


class AccuracyCheckSolver:
    contract = ACCURACY_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        constraints: List[AccuracyConstraint] = mapping["constraints"]
        issues = []
        # 每条 constraint 与每行做笛卡尔积；任何 predicate 抛异常
        # （例如读了不存在的列、除以 0）都视为该行不通过 → 不影响
        # 后续 constraint 继续跑
        for c in constraints:
            name = c.get("name", "(unnamed)")
            cols = c.get("columns", [])
            pred = c["predicate"]
            msg = c.get("message", f"failed constraint {name}")
            for idx, row in df.iterrows():
                try:
                    ok = bool(pred(row))
                except Exception:
                    ok = False
                if not ok:
                    issues.append({
                        "row_index":  int(idx),
                        "constraint": name,
                        "columns":    ",".join(map(str, cols)),
                        "values":     " | ".join(f"{c}={row.get(c)}"
                                                    for c in cols),
                        "message":    msg,
                    })
        out = pd.DataFrame(issues, columns=["row_index", "constraint",
                                              "columns", "values", "message"])
        ip = Path(output_dir) / ACCURACY_CONTRACT.output_files["issues_csv"]
        out.to_csv(ip, index=False)
        summary = {
            "n_rows":     int(len(df)),
            "n_constraints": int(len(constraints)),
            "n_issues":   int(len(out)),
            "by_constraint": out["constraint"].value_counts().to_dict()
                             if not out.empty else {},
            "is_accurate": bool(out.empty),
        }
        sp = Path(output_dir) / ACCURACY_CONTRACT.output_files["summary_json"]
        sp.write_text(__import__("json").dumps(summary, ensure_ascii=False,
                                                  indent=2),
                       encoding="utf-8")
        return {"issues_csv":   str(ip),
                "summary_json": str(sp),
                "summary_dict": summary,
                "issues_df":    out}


def get_consistency_solver(): return ConsistencyCheckSolver()
def get_accuracy_solver():    return AccuracyCheckSolver()


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """Plant 4 specific issues and verify each is caught exactly once.

    中文：fixture 里植入 4 类一致性问题 + 1 类准确性问题，验证每类
    都被精确抓到（不多不少）。

    一致性 fixture：4 行 patient 数据，刻意制造：
      - 重复主键 P001 出现 2 次 → 期望 duplicate_id 计 2 行
      - PXX001 不匹配 ``P\\d{3}``       → 期望 regex_mismatch 计 1
      - age=200 越出 (0, 120) 范围      → 期望 out_of_range 计 1
      - sex='X' 不在 ['M','F']           → 期望 value_not_allowed 计 1

    准确性 fixture：约束 BMI ≈ weight / height² 容差 0.5；
      - 第 4 行 bmi=99.9 与计算值 ≈20.76 严重不符 → 期望 1 个 issue

    通过判定：5 类问题各自计数与上面期望完全一致。
    """
    import tempfile

    df = pd.DataFrame([
        # row 0: ok
        {"PatientID": "P001", "age": 35, "sex": "M",
         "weight": 70, "height": 1.75, "bmi": 22.86},
        # row 1: duplicate ID + age out of range (200)
        {"PatientID": "P001", "age": 200, "sex": "F",
         "weight": 60, "height": 1.65, "bmi": 22.04},
        # row 2: regex mismatch (PXX001 not P\d{3})
        {"PatientID": "PXX001", "age": 40, "sex": "M",
         "weight": 80, "height": 1.80, "bmi": 24.69},
        # row 3: sex not allowed; bmi inconsistent with w/h^2 → accuracy
        {"PatientID": "P003", "age": 30, "sex": "X",
         "weight": 60, "height": 1.70, "bmi": 99.9},
    ])
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # consistency
        cs = get_consistency_solver().run(
            df=df,
            mapping=ColumnMapping({
                "id_col": "PatientID",
                "regex_rules": {"PatientID": r"P\d{3}"},
                "range_rules": {"age": (0, 120)},
                "allowed_values": {"sex": ["M", "F"]},
            }),
            output_dir=tmp,
        )
        bt = cs["summary_dict"]["by_issue_type"]
        if bt.get("duplicate_id", 0) != 2:
            diffs.append(f"expected 2 duplicate_id rows, got "
                         f"{bt.get('duplicate_id', 0)}")
        if bt.get("regex_mismatch", 0) != 1:
            diffs.append(f"expected 1 regex_mismatch, got "
                         f"{bt.get('regex_mismatch', 0)}")
        if bt.get("out_of_range", 0) != 1:
            diffs.append(f"expected 1 out_of_range (age=200), got "
                         f"{bt.get('out_of_range', 0)}")
        if bt.get("value_not_allowed", 0) != 1:
            diffs.append(f"expected 1 value_not_allowed (sex=X), got "
                         f"{bt.get('value_not_allowed', 0)}")

        # accuracy: BMI ≈ weight / height^2 within 0.5
        ac = get_accuracy_solver().run(
            df=df,
            mapping=ColumnMapping({
                "constraints": [
                    {
                        "name":      "bmi_consistency",
                        "columns":   ["weight", "height", "bmi"],
                        "predicate": (lambda r:
                            abs(r["bmi"] -
                                (r["weight"] / (r["height"] ** 2))) < 0.5),
                        "message":   "bmi disagrees with weight/height²",
                    },
                ],
            }),
            output_dir=tmp,
        )
        if ac["summary_dict"]["n_issues"] != 1:
            diffs.append(f"accuracy: expected 1 BMI mismatch, got "
                         f"{ac['summary_dict']['n_issues']}")

    return {"ok": len(diffs) == 0,
            "summary": ("planted 4 consistency + 1 accuracy issues all "
                        "caught exactly" if not diffs
                        else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["consistency_check",
                                    "accuracy_check"]}}
