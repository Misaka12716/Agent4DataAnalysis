from typing import Any, Dict, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from configs.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_MODEL
from utils.model_logger import log_model_event


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
    将工作区 Excel 元数据（与 JSON 同构的嵌套 dict/list）转为多级 Markdown 标题与列表，
    便于规划模型阅读；层级随深度递增，最高到 ######。
    """
    level = min(2 + depth, 6)
    bars = "#" * level

    if isinstance(obj, dict):
        chunks: list[str] = []
        for key, val in obj.items():
            chunks.append(f"{bars} {key}\n\n")
            if isinstance(val, (dict, list)):
                chunks.append(workspace_excel_info_to_structured_markdown(val, depth + 1))
            else:
                chunks.append(scalar_or_long_text_block(val))
        return "".join(chunks)
    if isinstance(obj, list):
        if not obj:
            return "（空）\n\n"
        if all(not isinstance(x, (dict, list)) for x in obj):
            return "".join(f"- {x}\n" for x in obj) + "\n"
        chunks: list[str] = []
        for i, item in enumerate(obj, 1):
            chunks.append(f"{bars} 第 {i} 项\n\n")
            chunks.append(workspace_excel_info_to_structured_markdown(item, depth + 1))
        return "".join(chunks)
    return scalar_or_long_text_block(obj)


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

    log_model_event(
        dialogue_id=session_id,
        stage=f"{stage_base}_input",
        content=user,
    )

    full_content = ""
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
