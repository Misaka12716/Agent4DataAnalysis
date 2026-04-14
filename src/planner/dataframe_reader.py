import os
import io
import json
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts.chat import (
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from typing import Dict, Any, List, Optional, Tuple, TypedDict
from configs.config import (
    OPENAI_COMPATIBLE_API_BASE,
    API_KEY,
    DEFAULT_MODEL,
    DEFAULT_CODER_MODEL,
)
from utils.model_logger import log_model_event

# -------------------------- 全局配置 --------------------------
SAMPLING_THRESHOLD = 1000
SAMPLE_ROWS = 1000
LLM_ANALYSIS_ROWS = 8


# -------------------------- 状态结构 --------------------------
class AnalysisState(TypedDict, total=False):
    df: pd.DataFrame
    llm_header_result: Dict[str, Any]
    fixed_df: pd.DataFrame
    sample_df: pd.DataFrame
    basic_info: Dict[str, Any]
    stats_info: Dict[str, Any]
    report: str
    header_fix_info: str
    dialogue_id: str  # 可选，对话ID，用于日志文件命名


# -------------------------- 核心工具函数 --------------------------
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
1. 确定**数据区开始的行号**（行号从0开始，即表头行之后的第一行数据的行号）；
   - 示例1：如果行0是表头，行1开始是数据 → data_start_row=1；
   - 示例2：如果行0-1是嵌套表头，行2开始是数据 → data_start_row=2；
   - 示例3：如果原始列名就是正确表头，行0开始是数据 → data_start_row=0；
2. 确定**各列最终的表头名称**（合并嵌套表头为单一名称，避免空值/重复）；
   - 示例：嵌套表头行0是"个人信息"，行1是"姓名" → 最终表头为"个人信息-姓名"；
   - 要求：表头名简洁、唯一，无空值，用中文，数量和表格列数一致；
3. 给出**分析说明**，解释你的判断依据（如嵌套表头合并逻辑、错位表头修正原因）；
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

## 错误处理
如果表格为空或无法识别表头，返回success=false，其他字段填默认值（data_start_row=0，column_headers=原始列名，analysis_note="无法识别表头"）。
    """

    chat_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_template),
            ("human", human_template),
        ]
    )

    messages = chat_prompt.format_messages(table_content=table_content)
    response = llm.invoke(messages)

    try:
        result = json.loads(response.content.strip())
        required_fields = [
            "data_start_row",
            "column_headers",
            "analysis_note",
            "success",
        ]
        if not all(field in result for field in required_fields):
            raise ValueError("缺少核心字段")
        if (
            not isinstance(result["data_start_row"], int)
            or result["data_start_row"] < 0
        ):
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


# -------------------------- 列名去重函数 --------------------------
def dedup_column_names(columns: List[str]) -> List[str]:
    """
    自定义列名去重逻辑
    规则：重复的列名后添加数字后缀，如 "姓名" → "姓名_1" → "姓名_2"
    """
    deduped = []
    count = {}
    for col in columns:
        col_stripped = col.strip()
        if col_stripped not in count:
            count[col_stripped] = 0
            deduped.append(col_stripped)
        else:
            count[col_stripped] += 1
            deduped.append(f"{col_stripped}_{count[col_stripped]}")
    return deduped


def fix_df_by_llm_result(
    df: pd.DataFrame, llm_result: Dict[str, Any]
) -> tuple[pd.DataFrame, str]:
    if not llm_result["success"]:
        return df, llm_result["analysis_note"]

    data_start_row = llm_result["data_start_row"]
    new_headers = llm_result["column_headers"]
    analysis_note = llm_result["analysis_note"]

    if data_start_row >= len(df):
        fix_info = f"数据区行号{data_start_row}超过表格行数{len(df)}，未修正表头。分析说明：{analysis_note}"
        return df, fix_info

    fixed_df = df.iloc[data_start_row:].reset_index(drop=True)
    fixed_df.columns = new_headers
    # 自定义列名去重
    fixed_df.columns = dedup_column_names(fixed_df.columns.tolist())

    fix_info = f"""
表头修正完成：
- 数据区起始行号：{data_start_row}（删除了前{data_start_row}行表头）；
- 新表头：{fixed_df.columns.tolist()}；
- 分析说明：{analysis_note}；
- 修正后表格形状：{fixed_df.shape}（原始：{df.shape}）。
    """.strip()

    return fixed_df, fix_info


# -------------------------- 核心节点函数 --------------------------
def llm_header_analysis_node(state: Dict[str, Any]) -> Dict[str, Any]:
    df = state["df"]
    dialogue_id = state.get("dialogue_id", "")
    llm = ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0.1,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
    )
    # 日志：记录表头分析阶段输入
    log_model_event(
        dialogue_id=dialogue_id,
        stage="dataframe_llm_header_input",
        content={"head_preview": extract_table_with_coords(df, LLM_ANALYSIS_ROWS)},
    )

    llm_result = llm_analyze_header(df, llm)

    # 日志：记录表头分析阶段输出
    log_model_event(
        dialogue_id=dialogue_id,
        stage="dataframe_llm_header_output",
        content=llm_result,
    )

    print(
        f"【LLM表头分析结果】\n{json.dumps(llm_result, ensure_ascii=False, indent=2)}"
    )
    return {"llm_header_result": llm_result}


def preprocess_node(state: Dict[str, Any]) -> Dict[str, Any]:
    raw_df = state["df"].copy()
    llm_result = state["llm_header_result"]

    fixed_df, header_fix_info = fix_df_by_llm_result(raw_df, llm_result)

    n_rows, n_cols = fixed_df.shape
    if n_rows > SAMPLING_THRESHOLD:
        sample_df = fixed_df.sample(n=min(SAMPLE_ROWS, n_rows), random_state=42)
        print(f"检测到大表格（{n_rows}行），已抽样至{len(sample_df)}行进行分析")
    else:
        sample_df = fixed_df.copy()
        print(f"表格规模适中（{n_rows}行），使用全量数据分析")

    basic_info = {
        "original_shape": raw_df.shape,
        "fixed_shape": fixed_df.shape,
        "columns": fixed_df.columns.tolist(),
        "is_large_table": n_rows > SAMPLING_THRESHOLD,
        "sampling_info": (
            f"抽样{len(sample_df)}/{n_rows}行"
            if n_rows > SAMPLING_THRESHOLD
            else "未抽样"
        ),
        "header_fix_info": header_fix_info,
    }

    return {
        "fixed_df": fixed_df,
        "header_fix_info": header_fix_info,
        "sample_df": sample_df,
        "basic_info": basic_info,
    }


def stats_analysis_node(state: Dict[str, Any]) -> Dict[str, Any]:
    sample_df = state["sample_df"]
    basic_info = state["basic_info"]

    # 缺失值分析
    missing_info = (sample_df.isnull().sum() / len(sample_df) * 100).round(2).to_dict()
    missing_info = {col: f"{val}%" for col, val in missing_info.items()}

    # 数值列转换和统计
    numeric_cols = []
    numeric_stats = {}
    for col in sample_df.columns:
        try:
            if sample_df[col].dtype == "object":
                test_series = pd.to_numeric(sample_df[col], errors="coerce")
                if test_series.notna().sum() / len(test_series) > 0.9:
                    sample_df[col] = test_series
        except:
            pass
    numeric_cols = sample_df.select_dtypes(include=[np.number]).columns.tolist()
    if numeric_cols:
        stats = sample_df[numeric_cols].describe().round(2)
        for col in numeric_cols:
            numeric_stats[col] = {
                "mean": stats[col]["mean"],
                "median": stats[col]["50%"],
                "min": stats[col]["min"],
                "max": stats[col]["max"],
                "std": stats[col]["std"],
            }

    # 类别列分析
    categorical_cols = sample_df.select_dtypes(
        include=["object", "category"]
    ).columns.tolist()
    categorical_stats = {}
    if categorical_cols:
        for col in categorical_cols:
            unique_count = sample_df[col].nunique()
            categorical_stats[col] = {
                "unique_values_count": unique_count,
                "top_3_values": sample_df[col].value_counts().head(3).to_dict(),
            }

    stats_info = {
        "missing_values": missing_info,
        "numeric_columns": numeric_cols,
        "numeric_statistics": numeric_stats,
        "categorical_columns": categorical_cols,
        "categorical_statistics": categorical_stats,
    }

    return {"stats_info": stats_info}


# -------------------------- 关键修改：generate_report_node 函数 --------------------------
def generate_report_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    修正：state是字典，必须用键值访问（state['key']）而非点语法（state.key）
    返回值改为字典格式，符合LangGraph节点要求
    """
    # 1. 用字典键值方式访问state中的数据
    basic_info = state["basic_info"]
    stats_info = state["stats_info"]
    header_fix_info = state["header_fix_info"]
    dialogue_id = state.get("dialogue_id", "")

    # 2. 构建Prompt（逻辑不变）
    prompt = f"""
请你作为数据分析师，分析以下表格的构成信息，并生成一份清晰、易懂的分析报告。
注意：表格可能是大表格，分析基于抽样数据（但列信息是完整的）；已通过大模型智能识别并修正表头（支持嵌套表头/错位表头）。

## 基础信息
- 原始表格形状：{basic_info['original_shape'][0]}行 × {basic_info['original_shape'][1]}列
- 修正后表格形状：{basic_info['fixed_shape'][0]}行 × {basic_info['fixed_shape'][1]}列
- 是否为大表格：{"是" if basic_info['is_large_table'] else "否"}
- 抽样信息：{basic_info['sampling_info']}
- 表头修正详情：{header_fix_info}

## 统计分析信息
1. 缺失值比例（按列）：{stats_info['missing_values']}
2. 数值列（{len(stats_info['numeric_columns'])}个）：{stats_info['numeric_columns']}
   数值列统计量：{stats_info['numeric_statistics']}
3. 类别列（{len(stats_info['categorical_columns'])}个）：{stats_info['categorical_columns']}
   类别列唯一值统计：{stats_info['categorical_statistics']}

## 报告要求
1. 语言简洁明了，分点说明；
2. 重点突出：表头修正逻辑（尤其是嵌套/错位表头）、表格核心构成、列类型与统计特征；
3. 若为大表格，需说明分析基于抽样数据；
4. 避免使用过于专业的术语，让非技术人员也能理解。
    """

    # 3. 调用LLM生成报告（逻辑不变）
    llm = ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0.1,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
    )
    # 日志：记录报告阶段输入
    log_model_event(
        dialogue_id=dialogue_id,
        stage="dataframe_report_input",
        content={"prompt": prompt},
    )

    response = llm.invoke(prompt)
    report = response.content

    # 日志：记录报告阶段输出
    log_model_event(
        dialogue_id=dialogue_id,
        stage="dataframe_report_output",
        content={"report": report},
    )

    # 4. 修正：返回字典格式（而非直接修改state），符合LangGraph节点规范
    return {"report": report}


# -------------------------- 构建智能体 --------------------------
def build_table_analysis_agent():
    graph = StateGraph(AnalysisState)
    graph.add_node("llm_header_analysis", llm_header_analysis_node)
    graph.add_node("preprocess", preprocess_node)
    graph.add_node("stats_analysis", stats_analysis_node)
    graph.add_node("generate_report", generate_report_node)

    graph.set_entry_point("llm_header_analysis")
    graph.add_edge("llm_header_analysis", "preprocess")
    graph.add_edge("preprocess", "stats_analysis")
    graph.add_edge("stats_analysis", "generate_report")
    graph.add_edge("generate_report", END)

    return graph.compile()


# -------------------------- 工作区 Excel Schema/样本（供 Planner 规划前调用） --------------------------
def read_workspace_excel_schema_and_sample(
    input_dir_abs_path: str,
) -> Dict[str, Any]:
    """
    读取指定目录（通常为工作区根目录）下所有 Excel 文件（包含子目录），返回每张表的 Schema 与 pandas info（不做 LLM 分析）。
    供 Planner 与 Coder 获取数据上下文。
    :param input_dir_abs_path: 目录绝对路径（通常为工作区根目录）
    :return: {
        "files": {
            "relative/path.xlsx": {
                "relative_path": "relative/path.xlsx",
                "columns": [...],
                "shape": (r,c),
                "pandas_info": "..."
            },
            ...
        },
        "summary": "共 N 个 Excel 文件，..."
    }
    """
    result: Dict[str, Any] = {"files": {}, "summary": ""}
    if not os.path.isdir(input_dir_abs_path):
        result["summary"] = "目录不存在或不可读"
        return result

    import glob

    # 递归查找所有 .xlsx，统一转换为相对于 input_dir_abs_path 的相对路径，供 LLM 直接使用
    pattern = os.path.join(input_dir_abs_path, "**", "*.xlsx")
    xlsx_files = glob.glob(pattern, recursive=True)
    for fp in xlsx_files:
        rel_path = os.path.relpath(fp, input_dir_abs_path).replace(os.sep, "/")
        try:
            df = pd.read_excel(fp, header=None)
            if df.empty:
                result["files"][rel_path] = {
                    "relative_path": rel_path,
                    "columns": [],
                    "shape": (0, 0),
                }
                continue
            # 第一行作为列名（简单策略；复杂表头可由后续 LLM 流程处理）
            df.columns = [str(c) for c in df.iloc[0]]
            df = df.iloc[1:].reset_index(drop=True)
            cols = df.columns.tolist()
            buf = io.StringIO()
            try:
                df.info(buf=buf, memory_usage=False)
                pandas_info = buf.getvalue()
            except Exception:
                pandas_info = ""
            result["files"][rel_path] = {
                "relative_path": rel_path,
                "columns": cols,
                "shape": df.shape,
                "pandas_info": pandas_info,
            }
        except Exception as e:
            result["files"][rel_path] = {
                "relative_path": rel_path,
                "error": str(e),
                "columns": [],
            }

    n = len(result["files"])
    result["summary"] = f"共 {n} 个 Excel 文件" if n else "未发现 .xlsx 文件"
    return result


if __name__ == "__main__":
    # 定义test.xlsx的路径（当前文件夹）
    excel_path = os.path.join(os.path.dirname(__file__), "test.xlsx")

    print("=== 测试案例 ===")

    # 读取Excel文件，添加异常处理
    try:
        # 读取Excel文件（不指定header，保留原始表头结构，方便LLM分析）
        df = pd.read_excel(excel_path, header=None)
        print(f"成功读取{excel_path}")
        print(f"原始表格形状：{df.shape}")

    except FileNotFoundError:
        print(f"错误：未找到文件 {excel_path}")
        print("请确保test.xlsx文件放在当前脚本所在文件夹下！")
        exit(1)
    except Exception as e:
        print(f"读取Excel文件失败：{str(e)}")
        exit(1)

    # 初始化智能体并运行
    try:
        agent = build_table_analysis_agent()
        initial_state = AnalysisState(df=df)
        final_state = agent.invoke(initial_state)

        # 输出最终报告
        print("\n=== 最终分析报告 ===")
        print(final_state["report"])
    except Exception as e:
        print(f"分析表格失败：{str(e)}")
