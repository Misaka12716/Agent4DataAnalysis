from typing import Any, Dict, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from configs.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_MODEL
from utils.model_logger import log_model_event, log_milestone, log_phase_end, log_phase_start


def scalar_or_long_text_block(val: Any) -> str:
    """标量一行输出；较长或多行字符串用 fenced 代码块，避免挤在标题下难以阅读。"""
    if val is None:
        return "（无）\n\n"
    if isinstance(val, bool):
        return ("是" if val else "否") + "\n\n"
    if isinstance(val, (int, float)):
        return f"{val}\n\n"
    if isinstance(val, str):
        s = val.strip()
        if len(s) > 240 or "\n" in val:
            return f"```\n{val.rstrip()}\n```\n\n"
        return f"{val}\n\n"
    return f"{val}\n\n"


def workspace_excel_info_to_structured_markdown(obj: Any, depth: int = 0) -> str:
    """
    已弃用：请使用 reader.formatters.workspace_digest_to_markdown。
    仍接受 excel_schema 或 workspace_digest 形状的 dict。
    """
    from reader.formatters import workspace_digest_to_markdown

    if isinstance(obj, dict) and "files" in obj and not any(
        isinstance(v, dict) and v.get("file_type") for v in (obj.get("files") or {}).values()
    ):
        wrapped = {"summary": obj.get("summary", ""), "files": obj.get("files") or {}}
        return workspace_digest_to_markdown(wrapped, depth=depth)
    return workspace_digest_to_markdown(obj, depth=depth)


def create_llm(streaming: bool = True) -> ChatOpenAI:
    """创建 LangChain ChatOpenAI 实例。"""
    return ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0.3,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
        streaming=streaming,
    )


def combine_plan_text(requirement_analysis: str, steps_outline: str) -> str:
    ra = (requirement_analysis or "").strip()
    so = (steps_outline or "").strip()
    if not ra and not so:
        return ""
    return f"## 需求解析\n\n{ra}\n\n## 步骤分解\n\n{so}"


async def run_planner_chain_stream(
    *,
    system: str,
    user: str,
    session_id: str,
    stage_base: str,
    stream_callback,
) -> str:
    """LangChain：ChatPromptTemplate | LLM，流式聚合全文。"""
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "{system}"),
            ("human", "{user}"),
        ]
    )
    llm = create_llm(streaming=True)
    chain = prompt | llm

    log_phase_start(
        session_id,
        f"{stage_base}_llm",
        "开始 Planner 单步 LLM 流式调用（本步内的输入/输出见下方 *_input / *_output）",
    )
    log_model_event(
        dialogue_id=session_id,
        stage=f"{stage_base}_input",
        content=user,
    )

    full_content = ""
    log_milestone(session_id, f"{stage_base}_llm", "astream 已开始，正在聚合 token")
    async for chunk in chain.astream({"system": system, "user": user}):
        token = chunk.content if hasattr(chunk, "content") else getattr(chunk, "content", "") or ""
        if not token:
            continue
        full_content += token
        if stream_callback:
            await stream_callback("llm_chunk", {"content": token})

    log_model_event(
        dialogue_id=session_id,
        stage=f"{stage_base}_output",
        content=full_content,
    )
    log_phase_end(
        session_id,
        f"{stage_base}_llm",
        {"output_chars": len(full_content), "status": "ok"},
    )
    if stream_callback:
        await stream_callback("llm_complete", {"content": full_content})

    return full_content.strip()


def finalize_plan_from_graph_state(state: Optional[Dict[str, Any]]) -> tuple[str, Optional[str], str, str]:
    """
    从图结束状态取出 plan_text、error、requirement_analysis、steps_outline。
    """
    if not state:
        return "", "规划流程未返回状态", "", ""
    err = state.get("error")
    ra = "" if state.get("requirement_analysis") is None else str(state.get("requirement_analysis")).strip()
    so = "" if state.get("steps_outline") is None else str(state.get("steps_outline")).strip()
    pt = state.get("plan_text")
    pt = "" if pt is None else str(pt).strip()
    if not pt and (ra or so):
        pt = combine_plan_text(ra, so)
    return pt, err if err else None, ra, so


def route_after_analyze(state: Dict[str, Any]) -> str:
    if state.get("error"):
        return "end"
    return "decompose"
