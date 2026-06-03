import io
import json
import os
from typing import Any, Dict, List, Optional

import pandas as pd
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from configs.config import (
    API_KEY,
    DEFAULT_MODEL,
    OPENAI_COMPATIBLE_API_BASE,
    READER_ENABLE_LLM_TABLE_HEADER,
    READER_TABLE_SAMPLE_ROWS,
)

LLM_ANALYSIS_ROWS = 8


def dedup_column_names(columns: List[str]) -> List[str]:
    deduped: List[str] = []
    count: Dict[str, int] = {}
    for col in columns:
        col_stripped = col.strip()
        if col_stripped not in count:
            count[col_stripped] = 0
            deduped.append(col_stripped)
        else:
            count[col_stripped] += 1
            deduped.append(f"{col_stripped}_{count[col_stripped]}")
    return deduped


def _header_cell_to_schema_column_name(cell: Any, col_index: int) -> str:
    if pd.isna(cell):
        return f"Unnamed: {col_index}"
    if isinstance(cell, str):
        stripped = cell.strip()
        if stripped == "":
            return f"Unnamed: {col_index}"
        return stripped
    return str(cell).strip()


def extract_table_with_coords(df: pd.DataFrame, max_rows: int = 8) -> str:
    extract_rows = min(max_rows, len(df))
    table_str = "表格前{}行数据（行号/列号从0开始）：\n".format(extract_rows)
    table_str += "原始列名（表头）：{}\n".format(df.columns.tolist())
    for row_idx in range(extract_rows):
        row_data = df.iloc[row_idx]
        table_str += f"行{row_idx}："
        for col_idx, col_name in enumerate(df.columns):
            cell_val = row_data[col_name]
            cell_val = "空值" if pd.isna(cell_val) else str(cell_val).strip()
            table_str += f"列{col_idx}='{cell_val}' | "
        table_str = table_str.rstrip(" | ") + "\n"
    return table_str


def llm_analyze_header(df: pd.DataFrame, llm: ChatOpenAI) -> Dict[str, Any]:
    table_content = extract_table_with_coords(df, LLM_ANALYSIS_ROWS)
    system_template = """
你是专业的数据分析师，擅长识别各种复杂表格的表头（包括错位表头、嵌套表头、多层表头）。
请严格按照以下要求分析表格并输出结果：
1. 确定**数据区开始的行号**（行号从0开始）；
2. 确定**各列最终的表头名称**（合并嵌套表头为单一名称，避免空值/重复）；
3. 给出**分析说明**；
4. 输出必须是严格的JSON格式，不添加任何额外文字。
"""
    human_template = """
## 输入表格信息
{table_content}

## 输出要求（必须严格遵守）
{{
    "data_start_row": 整数,
    "column_headers": ["列0表头", "列1表头", ...],
    "analysis_note": "你的分析说明",
    "success": true/false
}}
"""
    chat_prompt = ChatPromptTemplate.from_messages(
        [("system", system_template), ("human", human_template)]
    )
    messages = chat_prompt.format_messages(table_content=table_content)
    response = llm.invoke(messages)
    try:
        result = json.loads(response.content.strip())
        required_fields = ["data_start_row", "column_headers", "analysis_note", "success"]
        if not all(field in result for field in required_fields):
            raise ValueError("缺少核心字段")
        if not isinstance(result["data_start_row"], int) or result["data_start_row"] < 0:
            result["data_start_row"] = 0
        if len(result["column_headers"]) != len(df.columns):
            result["column_headers"] = df.columns.tolist()
        result["success"] = bool(result["success"])
    except Exception as e:
        result = {
            "data_start_row": 0,
            "column_headers": df.columns.tolist(),
            "analysis_note": f"LLM输出解析失败：{str(e)}，使用原始列名",
            "success": False,
        }
    return result


def fix_df_by_llm_result(df: pd.DataFrame, llm_result: Dict[str, Any]) -> tuple[pd.DataFrame, str]:
    if not llm_result["success"]:
        return df, llm_result["analysis_note"]
    data_start_row = llm_result["data_start_row"]
    new_headers = llm_result["column_headers"]
    analysis_note = llm_result["analysis_note"]
    if data_start_row >= len(df):
        fix_info = f"数据区行号{data_start_row}超过表格行数{len(df)}，未修正表头。"
        return df, fix_info
    fixed_df = df.iloc[data_start_row:].reset_index(drop=True)
    fixed_df.columns = dedup_column_names(new_headers)
    fix_info = (
        f"表头修正：数据区起始行={data_start_row}；新表头={fixed_df.columns.tolist()}；{analysis_note}"
    )
    return fixed_df, fix_info


def _serialize_sample_rows(df: pd.DataFrame, n: int) -> List[List[Any]]:
    if df.empty or n <= 0:
        return []
    head = df.head(n)
    rows: List[List[Any]] = []
    for _, row in head.iterrows():
        cells = []
        for v in row:
            if pd.isna(v):
                cells.append(None)
            elif hasattr(v, "item"):
                try:
                    cells.append(v.item())
                except Exception:
                    cells.append(str(v))
            else:
                cells.append(v)
        rows.append(cells)
    return rows


def _read_raw_table(fp: str, ext: str) -> pd.DataFrame:
    if ext in (".csv", ".tsv"):
        return pd.read_csv(fp, header=None, sep="\t" if ext == ".tsv" else ",")
    return pd.read_excel(fp, header=None)


def _schema_from_dataframe(
    df: pd.DataFrame,
    rel_path: str,
    fmt: str,
    *,
    header_analysis: Optional[Dict[str, Any]] = None,
    read_hint: Optional[str] = None,
) -> Dict[str, Any]:
    if df.empty:
        return {
            "file_type": "table",
            "format": fmt,
            "relative_path": rel_path,
            "columns": [],
            "shape": [0, 0],
            "sample_rows": [],
            "pandas_info": "",
        }
    cols = df.columns.tolist()
    buf = io.StringIO()
    try:
        df.info(buf=buf, memory_usage=False)
        pandas_info = buf.getvalue()
    except Exception:
        pandas_info = ""
    if read_hint is None:
        if fmt == "csv":
            read_hint = (
                "Use pd.read_csv(<relative_path>, header=0); column names match this schema."
            )
        else:
            read_hint = (
                "Use pd.read_excel(<relative_path>, header=0); column names match this schema "
                "(first row is headers; blank headers are Unnamed: 0, Unnamed: 1, …)."
            )
    entry: Dict[str, Any] = {
        "file_type": "table",
        "format": fmt,
        "relative_path": rel_path,
        "columns": cols,
        "shape": list(df.shape),
        "sample_rows": _serialize_sample_rows(df, READER_TABLE_SAMPLE_ROWS),
        "pandas_info": pandas_info,
        "read_hint": read_hint,
    }
    if header_analysis is not None:
        entry["header_analysis"] = header_analysis
    return entry


def digest_table_file(workspace_root: str, relative_path: str) -> Dict[str, Any]:
    fp = os.path.join(workspace_root, relative_path.replace("/", os.sep))
    ext = os.path.splitext(relative_path)[1].lower()
    fmt = ext.lstrip(".") or "unknown"
    try:
        raw_df = _read_raw_table(fp, ext)
        if raw_df.empty:
            return _schema_from_dataframe(raw_df, relative_path, fmt)

        header_analysis: Optional[Dict[str, Any]] = None
        if READER_ENABLE_LLM_TABLE_HEADER:
            llm = ChatOpenAI(
                model=DEFAULT_MODEL,
                temperature=0.1,
                api_key=API_KEY,
                base_url=OPENAI_COMPATIBLE_API_BASE,
            )
            header_analysis = llm_analyze_header(raw_df.copy(), llm)
            fixed_df, _ = fix_df_by_llm_result(raw_df.copy(), header_analysis)
            return _schema_from_dataframe(
                fixed_df, relative_path, fmt, header_analysis=header_analysis
            )

        row0 = raw_df.iloc[0]
        raw_names = [
            _header_cell_to_schema_column_name(row0.iloc[i], i) for i in range(len(row0))
        ]
        raw_df.columns = dedup_column_names(raw_names)
        df = raw_df.iloc[1:].reset_index(drop=True)
        return _schema_from_dataframe(df, relative_path, fmt)
    except Exception as e:
        return {
            "file_type": "table",
            "format": fmt,
            "relative_path": relative_path,
            "error": str(e),
            "columns": [],
        }
