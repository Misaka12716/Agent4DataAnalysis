# reporter/report_agent.py
# 汇总 Worker 执行结果，生成最终分析报告，支持流式输出。

from typing import AsyncGenerator, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from configs.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_MODEL
from configs.prompts import get_system_prompt, get_user_prompt
from utils.model_logger import log_model_event, log_phase_end, log_phase_start
from utils.session_memory import format_memory_for_prompt, read_session_memory_for_prompt


def _report_user_prompt(
    planner_summary: str,
    worker_results: Dict[str, Any],
    lang: str = "zh",
    session_memory: str = "",
) -> str:
    """从 configs.prompts 获取报告生成的用户提示并填入参数。"""
    logs = worker_results.get("logs", "")
    errors = worker_results.get("error_messages", [])
    success = worker_results.get("success", False)
    error_section = ""
    # 若执行标记成功但无任何日志输出，认为可能存在“仅定义函数未实际运行”的情况，给出提示
    if success and not logs:
        msg = "代码执行未产生任何可见输出，请检查是否编写了入口逻辑（如 demo 测试代码或 main 函数）。"
        errors = list(errors) if isinstance(errors, list) else [str(errors)]
        errors.append(msg)
    if errors:
        error_section = "\n- 错误信息:\n" + "\n".join(f"  - {e}" for e in errors)
    return get_user_prompt(
        "reporter",
        "report",
        lang=lang,
        planner_summary=planner_summary,
        success=success,
        execution_logs=logs,
        error_section=error_section,
        session_memory=session_memory or "",
    )


async def stream_report(
    planner_summary: str,
    worker_results: Dict[str, Any],
    lang: str = "zh",
    session_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    流式生成最终分析报告。
    :param planner_summary: Planner 阶段产出的任务/目标摘要文本
    :param worker_results: Worker 返回的 { success, logs, error_messages, results }
    :param lang: 语言
    :yield: 报告文本片段（用于 SSE 等流式推送）
    """
    sid = session_id or ""
    system_prompt = get_system_prompt("reporter", lang)
    mem = format_memory_for_prompt(read_session_memory_for_prompt(sid), lang)
    user_prompt = _report_user_prompt(planner_summary, worker_results, lang, session_memory=mem)
    log_phase_start(
        sid,
        "reporter_stream",
        {"planner_summary_chars": len(planner_summary or ""), "lang": lang},
    )
    # 日志：记录 Reporter 阶段输入
    log_model_event(
        dialogue_id=session_id or "",
        stage="reporter_input",
        content= system_prompt + "\n\n" + user_prompt,
    )
    
    llm = ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0.3,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
        streaming=True,
    )
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", "{user_prompt}"),
    ])
    
    chain = prompt | llm
    full_output = ""
    async for chunk in chain.astream({"user_prompt": user_prompt}):
        content = chunk.content if hasattr(chunk, "content") else chunk.get("content", "")
        if content:
            full_output += content
            yield content

    # 日志：记录 Reporter 阶段完整输出
    log_model_event(
        dialogue_id=session_id or "",
        stage="reporter_output",
        content= full_output,
    )
    log_phase_end(
        sid,
        "reporter_stream",
        {"output_chars": len(full_output), "status": "ok"},
    )
