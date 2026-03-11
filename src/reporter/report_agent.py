# reporter/report_agent.py
# 汇总 Worker 执行结果，生成最终分析报告，支持流式输出。

from typing import AsyncGenerator, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from utils.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_MODEL
from configs.prompts import get_system_prompt, get_user_prompt


def _report_user_prompt(planner_summary: str, worker_results: Dict[str, Any], lang: str = "zh") -> str:
    """从 configs.prompts 获取报告生成的用户提示并填入参数。"""
    logs = worker_results.get("logs", "")
    errors = worker_results.get("error_messages", [])
    success = worker_results.get("success", False)
    error_section = ""
    if errors:
        error_section = "\n- 错误信息:\n" + "\n".join(f"  - {e}" for e in errors)
    return get_user_prompt(
        "reporter", "report", lang=lang,
        planner_summary=planner_summary,
        success=success,
        execution_logs=logs,
        error_section=error_section,
    )


async def stream_report(
    planner_summary: str,
    worker_results: Dict[str, Any],
    lang: str = "zh",
) -> AsyncGenerator[str, None]:
    """
    流式生成最终分析报告。
    :param planner_summary: Planner 阶段产出的任务/目标摘要文本
    :param worker_results: Worker 返回的 { success, logs, error_messages, results }
    :param lang: 语言
    :yield: 报告文本片段（用于 SSE 等流式推送）
    """
    system_prompt = get_system_prompt("reporter", lang)
    user_prompt = _report_user_prompt(planner_summary, worker_results, lang)
    llm = ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0.3,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
        streaming=True,
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
    ])
    chain = prompt | llm
    async for chunk in chain.astream({"input": user_prompt}):
        content = chunk.content if hasattr(chunk, "content") else chunk.get("content", "")
        if content:
            yield content
