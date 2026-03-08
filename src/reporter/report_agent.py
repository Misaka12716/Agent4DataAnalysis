# reporter/report_agent.py
# 汇总 Worker 执行结果，生成最终分析报告，支持流式输出。

from typing import AsyncGenerator, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from utils.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_MODEL
from configs.prompts import get_system_prompt


def _build_report_prompt(planner_summary: str, worker_results: Dict[str, Any]) -> str:
    """根据规划摘要与执行结果构建报告生成用的用户提示。"""
    logs = worker_results.get("logs", "")
    errors = worker_results.get("error_messages", [])
    success = worker_results.get("success", False)
    prompt = f"""请根据以下规划与执行结果，生成一份简洁、结构清晰的数据分析报告。

## 规划摘要
{planner_summary}

## 执行结果
- 整体成功: {success}
- 执行日志:
{logs}
"""
    if errors:
        prompt += f"\n- 错误信息:\n" + "\n".join(f"  - {e}" for e in errors)
    prompt += "\n\n请用中文撰写报告，包含：1) 分析目标 2) 主要发现 3) 结论与建议。"
    return prompt


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
    user_prompt = _build_report_prompt(planner_summary, worker_results)
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
