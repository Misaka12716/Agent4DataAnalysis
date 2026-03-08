# coder/workspace_coder.py
# 基于工作区的代码生成：根据 Planner 的规划生成代码并写入工作区（不执行）。
# 使用 configs.prompts 与 utils.workspace_file_ops，路径均为相对路径。

import re
from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from utils.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_CODER_MODEL
from configs.prompts import get_system_prompt
from utils.workspace_file_ops import create_python_file


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
) -> str:
    """根据单任务描述生成纯 Python 代码（不写文件）。"""
    system_prompt = get_system_prompt("coder", lang)
    user_template = (
        "任务要求：{task_desc}\n"
        "输入变量名：{input_var_name}（说明：{input_var_desc}）\n"
        "输出变量名：{output_var_name}（说明：{output_var_desc}）\n"
        "请生成符合规范的 Python 代码，仅返回代码不要 markdown。"
    )
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
    raw = chain.invoke({
        "task_desc": task_desc,
        "input_var_name": input_var_name,
        "input_var_desc": input_var_desc,
        "output_var_name": output_var_name,
        "output_var_desc": output_var_desc,
    })
    return clean_code_from_markdown(raw)


def generate_and_write_code(
    session_id: str,
    code_specs: List[Dict[str, Any]],
    lang: str = "zh",
) -> List[Dict[str, Any]]:
    """
    根据规划生成代码并写入工作区。
    :param session_id: 会话 ID，用于解析工作区
    :param code_specs: 每个元素至少包含 task_desc, input_var_name, input_var_desc,
                       output_var_name, output_var_desc, relative_path (如 "code/task_1.py")
    :param lang: 提示词语言
    :return: [ {"relative_path": str, "success": bool, "error": str|None }, ... ]
    """
    results = []
    for spec in code_specs:
        rel_path = spec.get("relative_path", "code/main.py")
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
