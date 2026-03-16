# coder/workspace_coder.py
# 基于工作区的代码生成：根据 Planner 的规划生成代码并写入工作区（不执行）。
# 使用 configs.prompts 与 utils.workspace_file_ops，路径均为相对路径。

import re
import json
from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from utils.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_CODER_MODEL
from configs.prompts import get_coder_system_prompt
from utils.workspace_file_ops import create_python_file
from utils.model_logger import log_model_event


def _format_workspace_files_info(workspace_context: Optional[Dict[str, Any]], lang: str = "zh") -> str:
    """
    将工作区文件列表与 Excel 结构格式化为给 Coder 的说明文本，便于使用真实路径与格式编写代码。
    """
    if not workspace_context:
        return "" if lang == "zh" else ""
    file_list = workspace_context.get("file_list") or []
    excel_schema = workspace_context.get("excel_schema") or {}
    lines = []
    if lang == "zh":
        lines.append("## 工作区根目录下的文件与数据格式（必须使用以下真实路径与格式，禁止编造假数据或假路径）")
        lines.append("当前工作区根目录中的文件列表（相对路径）：")
        lines.append(json.dumps(file_list, ensure_ascii=False))
        files_detail = excel_schema.get("files") or {}
        if files_detail:
            lines.append("其中 Excel 文件的结构与样本（列名、类型、前几行）如下，读取时请按此格式使用：")
            lines.append(json.dumps(files_detail, ensure_ascii=False, default=str, indent=2))
    else:
        lines.append("## Workspace files and data format (you must use these real paths and formats; do not fabricate paths or data)")
        lines.append("File list in workspace root (relative paths):")
        lines.append(json.dumps(file_list, ensure_ascii=False))
        files_detail = excel_schema.get("files") or {}
        if files_detail:
            lines.append("Excel file schemas and sample rows (use these for reading):")
            lines.append(json.dumps(files_detail, ensure_ascii=False, default=str, indent=2))
    return "\n".join(lines)


def clean_code_from_markdown(code_str: str) -> str:
    """清理代码中的 Markdown 包裹，只保留纯 Python。"""
    if not code_str:
        return ""
    code_str = re.sub(r"^```python\s*", "", code_str, flags=re.MULTILINE)
    code_str = re.sub(r"^```\s*", "", code_str, flags=re.MULTILINE)
    code_str = re.sub(r"\s*```$", "", code_str)
    return code_str.strip()


def _generate_code_for_task(
    task_desc: str,
    input_var_name: str,
    input_var_desc: str,
    output_var_name: str,
    output_var_desc: str,
    lang: str = "zh",
    workspace_context: Optional[Dict[str, Any]] = None,
    dialogue_id: str = "",
) -> str:
    """根据单任务描述生成纯 Python 代码（不写文件）。"""
    # 使用专门的 Coder system 提示，强制包含 demo 测试逻辑（即实际执行入口）
    system_prompt = get_coder_system_prompt(
        "generate",
        lang=lang,
        input_var_name=input_var_name,
        input_var_desc=input_var_desc,
        output_var_name=output_var_name,
        output_var_desc=output_var_desc,
    )
    workspace_files_info = _format_workspace_files_info(workspace_context, lang)
    user_template = "任务要求：{task_desc}"
    if workspace_files_info:
        user_template += "\n\n{workspace_files_info}"
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", user_template),
    ])
    llm = ChatOpenAI(
        model=DEFAULT_CODER_MODEL,
        temperature=0.1,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
    )
    chain = prompt | llm | StrOutputParser()
    payload = {
        "task_desc": task_desc,
        "input_var_name": input_var_name,
        "input_var_desc": input_var_desc,
        "output_var_name": output_var_name,
        "output_var_desc": output_var_desc,
    }
    if workspace_files_info:
        payload["workspace_files_info"] = workspace_files_info

    # 日志：记录 Coder 阶段输入
    log_model_event(
        dialogue_id=dialogue_id,
        stage="coder_generate_input",
        content={
            "system_prompt": system_prompt,
            "workspace_files_info": workspace_files_info,
            "payload": payload,
        },
    )

    raw = chain.invoke(payload)

    # 日志：记录 Coder 阶段原始输出
    log_model_event(
        dialogue_id=dialogue_id,
        stage="coder_generate_output",
        content={"raw_output": raw},
    )

    return clean_code_from_markdown(raw)


def generate_and_write_code(
    session_id: str,
    code_specs: List[Dict[str, Any]],
    lang: str = "zh",
    workspace_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    根据规划生成代码并写入工作区。
    :param session_id: 会话 ID，用于解析工作区
    :param code_specs: 每个元素至少包含 task_desc, input_var_name, input_var_desc,
                       output_var_name, output_var_desc, relative_path (如 "task_1.py")
    :param lang: 提示词语言
    :param workspace_context: 可选，工作区文件列表与 Excel 结构，供 Coder 使用真实路径与格式
    :return: [ {"relative_path": str, "success": bool, "error": str|None }, ... ]
    """
    results = []
    for spec in code_specs:
        rel_path = spec.get("relative_path", "main.py")
        if not rel_path.endswith(".py"):
            rel_path = rel_path.rstrip("/") + ".py"
        task_desc = spec.get("task_desc", "")
        input_name = spec.get("input_var_name", "input_data")
        input_desc = spec.get("input_var_desc", "输入数据")
        output_name = spec.get("output_var_name", "output_result")
        output_desc = spec.get("output_var_desc", "输出结果")
        try:
            code = _generate_code_for_task(
                task_desc=task_desc,
                input_var_name=input_name,
                input_var_desc=input_desc,
                output_var_name=output_name,
                output_var_desc=output_desc,
                lang=lang,
                workspace_context=workspace_context,
                dialogue_id=session_id,
            )
            ok = create_python_file(session_id, rel_path, code, overwrite=True)
            results.append({
                "relative_path": rel_path,
                "success": ok,
                "error": None if ok else "写入工作区失败",
            })
        except Exception as e:
            results.append({
                "relative_path": rel_path,
                "success": False,
                "error": str(e),
            })
    return results
