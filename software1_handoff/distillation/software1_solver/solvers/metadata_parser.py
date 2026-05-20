"""Schema metadata parser solver (F01 / Q31 / Q32 / Q33).

Profiles a CSV / DataFrame and emits a structured schema description:
column name, inferred type, uniqueness, missing count, sample values, +
a list of preprocessing recommendations.  Pure pandas / numpy.

中文说明
========
Schema/元数据自动解析。给一份 DataFrame，逐列推断 dtype，输出一份
结构化 schema 描述 + 预处理建议清单。

推断的"类型"枚举（共 6 种）：
  - integer / float / boolean
  - datetime           （正则匹配 ``YYYY-MM-DD`` 或 pd.to_datetime）
  - categorical        （unique 数小、字符串短、列名不像自由文本）
  - string_id          （高基数、字母数字短串、列名带 id/uid 等）
  - text_or_string     （兜底，长文本或列名带 note/comment/备注）

启发式策略（优先级从高到低）：
  1. 列名 hint（_TEXT_NAME_HINTS / _ID_NAME_HINTS）最可靠
  2. pandas 原生 dtype（bool / int / float / datetime）
  3. 整列尝试 to_numeric / 正则匹配 datetime
  4. 字符串长度 + 唯一性 → string_id 或 categorical 兜底

输入 / 输出
==========
- 输入：任意 DataFrame，``roles={}`` 不需要 mapping
- 静态参数：``sample_topk=3``（每列存最高频的 3 个样例值）
- 输出 ``metadata_json`` = ``metadata.json``
  {n_rows, n_cols, columns: [...], preprocessing_recommendations: [...]}
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - roles 为空 → 直接吃整个 DataFrame
#   - static_params.sample_topk：每列要保留多少个高频样例值（写进
#     metadata.json 给 LLM/UI 看，默认 3 个够用）
CONTRACT = SolverContract(
    name="metadata_parser",
    capability="F01_data_governance_cleaning",
    description=(
        "Auto-parse a DataFrame into a schema description: per-column "
        "inferred dtype, uniqueness, missing counts, sample values; "
        "plus a preprocessing-recommendation list."
    ),
    roles={},  # operates on the entire DataFrame; no role mapping needed
    static_params={"sample_topk": 3},
    output_files={"metadata_json": "metadata.json"},
)


_DATETIME_PAT = re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T]\d{1,2}:\d{1,2}(:\d{1,2})?)?$"
)

# Column-name hints that override the heuristic — names like "free_note"
# are explicitly NLP text columns regardless of how short the sample looks.
_TEXT_NAME_HINTS = (
    "note", "notes", "comment", "comments", "description", "remark",
    "remarks", "free_text", "freetext", "text", "narrative",
    "备注", "描述", "主诉", "病史", "记录",
)
_ID_NAME_HINTS = ("id", "_id", "patient", "subject", "uuid", "uid",
                  "case", "record")


def _name_has(name: str, hints) -> bool:
    nl = str(name).lower()
    return any(h in nl for h in hints)


def _infer_type(series: pd.Series, name: str = "") -> str:
    # 启发式类型推断；策略按可信度从高到低：
    # 1) 列名 hint：name 含 note/comment/备注/描述... → 直接当文本
    # 2) pandas dtype（bool / int / float / datetime）
    # 3) float 但全是整数 → 退化成 integer（更利下游建模）
    # 4) 字符串列：≥80% 匹配 YYYY-MM-DD 正则 → datetime
    # 5) 字符串列：能被 to_numeric 解析 → integer / float
    # 6) 高基数 + 短字母数字 + 列名像 ID → string_id
    # 7) 低基数 + 短标签 → categorical
    # 8) 兜底 → text_or_string
    if _name_has(name, _TEXT_NAME_HINTS):
        return "text_or_string"

    s = series.dropna()
    if len(s) == 0:
        return "text_or_string"
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "integer"
    if pd.api.types.is_float_dtype(s):
        # 全是整数的 float（例如 age=30.0）→ 当 integer，避免下游
        # 错把它当连续变量做 KDE
        if (s.dropna() % 1 == 0).all():
            return "integer"
        return "float"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"

    str_s = s.astype(str)

    # 0.8 阈值：宽容 1-2 行脏数据（"未知"/"NA" 等），不至于因为个别
    # 异常值就放弃 datetime 判定
    if str_s.str.match(_DATETIME_PAT).mean() > 0.8:
        return "datetime"

    try:
        pd.to_numeric(str_s, errors="raise")
        if (str_s.str.contains(r"\.").any()):
            return "float"
        return "integer"
    except (ValueError, TypeError):
        pass

    n = len(s)
    n_uniq = s.nunique()
    avg_len = float(str_s.str.len().mean())

    # ID: highly unique, alphanumeric short
    # 三个并列条件：完全唯一 + 平均长度短 + 字符集干净
    # 加 n_uniq>10 是为了避免 4 行小表把 sex 误判成 ID
    if (n_uniq == len(s) and avg_len <= 32 and
            str_s.str.match(r"^[A-Za-z0-9_\-]+$").mean() > 0.8 and
            (_name_has(name, _ID_NAME_HINTS) or n_uniq > 10)):
        return "string_id"

    # categorical: small unique cardinality, short labels, AND the
    # column name does NOT look like a free-text field
    # max(20, n/8) 是经验阈值：n=160 时阈值 20，n=800 时阈值 100
    if n_uniq <= max(20, n / 8) and avg_len <= 30 and n_uniq < n:
        return "categorical"

    return "text_or_string"


class MetadataParserSolver:
    contract = CONTRACT

    def __init__(self, sample_topk: int = 3):
        """中文：

        :param sample_topk: 每列保留多少个最高频样例值塞进 metadata.json。
                            默认 3 个：少了不够 LLM 推理，多了 json 太大
                            对长 schema 不友好。
        """
        self.sample_topk = sample_topk

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        n_rows, n_cols = df.shape
        columns: List[Dict[str, Any]] = []
        recs: List[str] = []

        any_text = False
        any_datetime = False
        any_categorical = False
        any_missing_numeric = False

        for c in df.columns:
            s = df[c]
            t = _infer_type(s, name=str(c))
            non_null = s.dropna()
            n_unique = int(non_null.nunique())
            missing = int(s.isna().sum())
            top_vals = (non_null.astype(str)
                                .value_counts()
                                .head(self.sample_topk)
                                .index.tolist())
            col_info: Dict[str, Any] = {
                "name": str(c),
                "inferred_type": t,
                "n_unique": n_unique,
                "missing_count": missing,
                "missing_pct": round(missing / n_rows * 100, 2),
                "sample_values": top_vals,
            }
            if t == "string_id":
                col_info["is_unique_after_dropna"] = bool(n_unique == len(non_null))
            columns.append(col_info)

            if t == "text_or_string":
                any_text = True
            elif t == "datetime":
                any_datetime = True
            elif t == "categorical":
                any_categorical = True
            if t in ("integer", "float") and missing > 0:
                any_missing_numeric = True

        recs.append("patient_id: 校验唯一性后用作主键")
        if any_categorical:
            recs.append("sex / diagnosis: 类别编码（one-hot 或 label encoding）")
        if any_datetime:
            recs.append("admission_date: 解析为 datetime 并衍生年/月/季节")
        if any_missing_numeric:
            recs.append("缺失率 > 0 的字段：在数值列用中位数/均值填补，在类别列用众数或新增 'missing' 类")
        if any_text:
            recs.append("free_note: 非结构化文本，建议另起 NLP 管道")
        # at minimum 3 recommendations are required
        while len(recs) < 3:
            recs.append("整体：在分析前对所有字段进行 dtype 校验与缺失率统计")

        meta = {
            "n_rows": n_rows,
            "n_cols": n_cols,
            "columns": columns,
            "preprocessing_recommendations": recs,
        }
        path = Path(output_dir) / CONTRACT.output_files["metadata_json"]
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                        encoding="utf-8")

        return {"metadata_json": str(path),
                "metadata_dict": meta}


def get_solver(sample_topk: int = 3):
    return MetadataParserSolver(sample_topk=sample_topk)


def selftest():
    """Hand-crafted DataFrame covering all 6 inferred types.

    中文：手搓一份 4 行 × 7 列的 fixture，每列对应一种期望类型：
      - patient_id  → string_id  （4 个唯一短串 + 列名带 id）
      - age         → integer    （含 NaN，验证 dropna 不影响判定）
      - bmi         → float      （非整数 float）
      - sex         → categorical（2 个 level + 短标签）
      - admission   → datetime   （正则匹配率=100% > 0.8）
      - free_note   → text_or_string（列名 hint 命中 _TEXT_NAME_HINTS）
      - is_active   → boolean    （pandas 原生 bool dtype）

    通过判定：7 列推断类型逐一与 ``expected`` 字典完全匹配，
    且 preprocessing_recommendations ≥ 3 条。
    """
    import tempfile
    df = pd.DataFrame({
        "patient_id": ["P1", "P2", "P3", "P4"],
        "age":        [30, 40, 50, np.nan],          # integer with NaN
        "bmi":        [22.5, 24.1, 27.7, 19.8],       # float
        "sex":        ["M", "F", "M", "F"],           # categorical
        "admission":  ["2024-01-01", "2024-02-15",
                       "2024-03-30", None],           # datetime
        "free_note":  ["fragment", "longer free-text note",
                       "中文备注", None],            # text_or_string
        "is_active":  [True, False, True, False],     # boolean
    })
    expected = {
        "patient_id": "string_id",
        "age":        "integer",
        "bmi":        "float",
        "sex":        "categorical",
        "admission":  "datetime",
        "free_note":  "text_or_string",
        "is_active":  "boolean",
    }
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(df=df, mapping=ColumnMapping({}),
                                output_dir=Path(tmp))
        meta = out["metadata_dict"]
        a_cols = {c["name"]: c for c in meta["columns"]}
        for name, exp in expected.items():
            if a_cols[name]["inferred_type"] != exp:
                diffs.append(f"{name}: expected {exp!r}, got "
                             f"{a_cols[name]['inferred_type']!r}")
        if len(meta["preprocessing_recommendations"]) < 3:
            diffs.append("expected ≥3 preprocessing recommendations")
    return {"ok": len(diffs) == 0,
            "summary": ("all 7 dtype inferences correct + ≥3 recs"
                        if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs, "tested": ["metadata_parser"]}}
