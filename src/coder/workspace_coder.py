# coder/workspace_coder.py
# 基于工作区的代码生成：按（数据文件信息 / 需求解析 / 步骤分解）三段输入生成**单个** Python 文件并写入工作区（不执行）。
# 使用 configs.prompts 与 utils.workspace_file_ops，路径均为相对路径。

import re
import json
from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from configs.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_CODER_MODEL
from configs.prompts import get_coder_system_prompt, get_user_prompt
from utils.workspace_file_ops import create_python_file, read_file
from utils.model_logger import log_milestone, log_model_event, log_phase_end, log_phase_start


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
            lines.append("其中 Excel 文件的结构、样本与 pandas.DataFrame.info() 摘要如下，读取时请按此格式使用：")
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
    lang: str = "zh",
    workspace_context: Optional[Dict[str, Any]] = None,
    dialogue_id: str = "",
    requirement_analysis: str = "",
    steps_outline: str = "",
) -> str:
    """根据（一）数据文件信息（二）需求解析（三）步骤分解生成纯 Python 代码（不写文件）。"""
    system_prompt = get_coder_system_prompt("generate", lang=lang)
    workspace_files_info = _format_workspace_files_info(workspace_context, lang)
    if not workspace_files_info.strip():
        workspace_files_info = (
            "（当前工作区无可用文件列表或 Excel 结构，请仅依据下方需求与步骤合理假设路径；若无法假设则报错说明。）"
            if lang == "zh"
            else "(No file list or Excel schema in workspace; infer paths from steps or fail clearly.)"
        )
    ra = (requirement_analysis or "").strip()
    so = (steps_outline or "").strip()
    if not ra and not so and (task_desc or "").strip():
        ra = (task_desc or "").strip()
        so = "（无单独步骤分解，请根据需求解析整体实现。）" if lang == "zh" else "(No separate step outline; implement from analysis.)"
    user_body = get_user_prompt(
        "coder",
        "generate",
        lang=lang,
        data_file_info=workspace_files_info,
        requirement_analysis=ra or "（空）",
        steps_outline=so or "（空）",
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", "{user_body}"),
    ])
    llm = ChatOpenAI(
        model=DEFAULT_CODER_MODEL,
        temperature=0.1,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
    )
    chain = prompt | llm | StrOutputParser()
    payload = {"user_body": user_body}

    log_milestone(
        dialogue_id,
        "coder_single_task",
        {"action": "generate_code", "relative_path_hint": "见后续写入路径"},
    )
    # 日志：记录 Coder 阶段输入
    log_model_event(
        dialogue_id=dialogue_id,
        stage="coder_generate_input",
        content= user_body,
    )

    raw = chain.invoke(payload)

    # 日志：记录 Coder 阶段原始输出
    log_model_event(
        dialogue_id=dialogue_id,
        stage="coder_generate_output",
        content= raw,
    )

    return clean_code_from_markdown(raw)


def correct_and_write_code(
    session_id: str,
    relative_path: str,
    error_msg: str,
    lang: str = "zh",
    workspace_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    读取工作区内已有 Python 文件，根据执行错误信息修正后写回。
    :return: {"relative_path", "success", "error": str|None}
    """
    rel_path = relative_path or "main.py"
    if not rel_path.endswith(".py"):
        rel_path = rel_path.rstrip("/") + ".py"
    existing = read_file(session_id, rel_path)
    if existing is None:
        return {
            "relative_path": rel_path,
            "success": False,
            "error": "无法读取工作区代码文件或文件不存在",
        }
    err = (error_msg or "").strip() or ("（无详细错误）" if lang == "zh" else "(no error detail)")
    system_prompt = get_coder_system_prompt(
        "correct",
        lang=lang,
        existing_code=existing,
        error_msg=err,
    )
    user_body = get_user_prompt("coder", "correct", lang=lang)
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", "{user_body}"),
    ])
    llm = ChatOpenAI(
        model=DEFAULT_CODER_MODEL,
        temperature=0.1,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
    )
    chain = prompt | llm | StrOutputParser()
    payload = {"user_body": user_body}
    log_phase_start(
        session_id,
        "coder_correct",
        {"relative_path": rel_path},
    )
    log_model_event(
        dialogue_id=session_id,
        stage="coder_correct_input",
        content=user_body + "\n\n" + err[:8000],
    )
    try:
        raw = chain.invoke(payload)
    except Exception as e:
        log_model_event(
            dialogue_id=session_id,
            stage="coder_correct_output",
            content=f"invoke_error: {e}",
        )
        log_phase_end(
            session_id,
            "coder_correct",
            {"relative_path": rel_path, "invoke_error": str(e)},
        )
        return {
            "relative_path": rel_path,
            "success": False,
            "error": str(e),
        }
    log_model_event(
        dialogue_id=session_id,
        stage="coder_correct_output",
        content=raw,
    )
    code = clean_code_from_markdown(raw)
    ok = create_python_file(session_id, rel_path, code, overwrite=True)
    log_phase_end(
        session_id,
        "coder_correct",
        {"relative_path": rel_path, "write_ok": ok},
    )
    return {
        "relative_path": rel_path,
        "success": ok,
        "error": None if ok else "写入工作区失败",
    }


def generate_and_write_code(
    session_id: str,
    code_specs: List[Dict[str, Any]],
    lang: str = "zh",
    workspace_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    根据规划生成代码并写入工作区。
    :param session_id: 会话 ID，用于解析工作区
    :param code_specs: 每个元素含 requirement_analysis、steps_outline（来自 Planner 两步输出），
                       可选 task_desc 作回退；relative_path 一般为 main.py
    :param lang: 提示词语言
    :param workspace_context: 可选，工作区文件列表与 Excel 结构，供 Coder 使用真实路径与格式
    :return: [ {"relative_path": str, "success": bool, "error": str|None }, ... ]
    """
    log_phase_start(
        session_id,
        "coder_batch",
        {"spec_count": len(code_specs)},
    )
    results = []
    for i, spec in enumerate(code_specs):
        rel_path = spec.get("relative_path", "main.py")
        if not rel_path.endswith(".py"):
            rel_path = rel_path.rstrip("/") + ".py"
        task_desc = spec.get("task_desc", "")
        req_a = spec.get("requirement_analysis", "")
        steps_o = spec.get("steps_outline", "")
        log_milestone(
            session_id,
            "coder_batch_item",
            {"index": i + 1, "total": len(code_specs), "relative_path": rel_path},
        )
        try:
            code = _generate_code_for_task(
                task_desc=task_desc,
                lang=lang,
                workspace_context=workspace_context,
                dialogue_id=session_id,
                requirement_analysis=req_a,
                steps_outline=steps_o,
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
    log_phase_end(
        session_id,
        "coder_batch",
        {
            "written": len(results),
            "all_success": all(r.get("success") for r in results) if results else True,
        },
    )
    return results
