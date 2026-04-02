# 使用 LangGraph 定义规划器流程：两步 LangChain 链路——需求解析 → 步骤分解（均不写具体代码）。
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from configs.prompts import (
    get_user_prompt,
    get_planner_step_system_prompt,
)
from utils.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_MODEL
from utils.model_logger import log_model_event
from utils.workspace_manager import resolve_workspace_root
from utils.dataframe_reader import read_workspace_excel_schema_and_sample
from db.session_store import SessionStore

from typing import Dict, Any, AsyncGenerator, Optional
import asyncio
import json
import os


def _create_llm(streaming: bool = True) -> ChatOpenAI:
    """创建 LangChain ChatOpenAI 实例。"""
    return ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0.3,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
        streaming=streaming,
    )


def _combine_plan_text(requirement_analysis: str, steps_outline: str) -> str:
    ra = (requirement_analysis or "").strip()
    so = (steps_outline or "").strip()
    if not ra and not so:
        return ""
    return f"## 需求解析\n\n{ra}\n\n## 步骤分解\n\n{so}"


async def _run_planner_chain_stream(
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
    llm = _create_llm(streaming=True)
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


def _finalize_plan_from_graph_state(state: Optional[Dict[str, Any]]) -> tuple[str, Optional[str], str, str]:
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
        pt = _combine_plan_text(ra, so)
    return pt, err if err else None, ra, so


def _route_after_analyze(state: Dict[str, Any]) -> str:
    if state.get("error"):
        return "end"
    return "decompose"


# -------------------------- 节点：第一步 需求解析 --------------------------
async def _node_analyze(state: Dict[str, Any]) -> Dict[str, Any]:
    cb = state.get("stream_callback")
    session_id = state.get("session_id", "")
    lang = state.get("lang", "zh")
    input_data = state["input_requirement"]
    file_info = state.get("file_info") or "No files uploaded"

    if cb:
        await cb("state", {"node": "analyze"})

    user_prompt = get_user_prompt(
        "planner",
        "analyze_requirement",
        lang=lang,
        input_data=input_data,
        file_info=file_info,
    )
    system = get_planner_step_system_prompt("analyze", lang=lang)

    full_content = await _run_planner_chain_stream(
        system=system,
        user=user_prompt.strip(),
        session_id=session_id,
        stage_base="planner_analyze",
        stream_callback=cb,
    )

    if not full_content:
        return {
            "requirement_analysis": None,
            "error": "规划失败：需求解析输出为空",
            "input_requirement": input_data,
            "file_info": file_info,
            "lang": lang,
            "session_id": session_id,
            "stream_callback": cb,
        }

    return {
        "requirement_analysis": full_content,
        "error": None,
        "input_requirement": input_data,
        "file_info": file_info,
        "lang": lang,
        "session_id": session_id,
        "stream_callback": cb,
    }


# -------------------------- 节点：第二步 步骤分解 --------------------------
async def _node_decompose(state: Dict[str, Any]) -> Dict[str, Any]:
    cb = state.get("stream_callback")
    session_id = state.get("session_id", "")
    lang = state.get("lang", "zh")
    input_data = (state.get("input_requirement") or "").strip()
    file_info = state.get("file_info") or "No files uploaded"
    requirement_analysis = (state.get("requirement_analysis") or "").strip()

    if not input_data:
        return {
            "steps_outline": None,
            "plan_text": None,
            "error": "规划失败：步骤分解缺少 input_requirement",
        }

    if cb:
        await cb("state", {"node": "decompose"})

    user_prompt = get_user_prompt(
        "planner",
        "decompose_steps",
        lang=lang,
        input_data=input_data,
        file_info=file_info,
        requirement_analysis=requirement_analysis,
    )
    system = get_planner_step_system_prompt("decompose", lang=lang)

    full_content = await _run_planner_chain_stream(
        system=system,
        user=user_prompt.strip(),
        session_id=session_id,
        stage_base="planner_decompose",
        stream_callback=cb,
    )

    if not full_content:
        return {
            "steps_outline": None,
            "plan_text": None,
            "error": "规划失败：步骤分解输出为空",
        }

    steps_outline = full_content
    plan_text = _combine_plan_text(requirement_analysis, steps_outline)
    return {
        "requirement_analysis": requirement_analysis,
        "steps_outline": steps_outline,
        "plan_text": plan_text,
        "error": None,
    }


def _build_planner_graph():
    """LangGraph：analyze -> (条件) decompose -> END 或 analyze 失败 -> END。"""
    graph_builder = StateGraph(dict)
    graph_builder.add_node("analyze", _node_analyze)
    graph_builder.add_node("decompose", _node_decompose)
    graph_builder.set_entry_point("analyze")
    graph_builder.add_conditional_edges(
        "analyze",
        _route_after_analyze,
        {"decompose": "decompose", "end": END},
    )
    graph_builder.add_edge("decompose", END)
    return graph_builder.compile()


# -------------------------- AgentPlanner 类 --------------------------
class AgentPlanner:
    """
    规划器：两步 LangChain 调用——需求解析、步骤分解。
    仅负责规划输出，不负责任务分配。
    """

    def __init__(self):
        self.lang = "zh"
        self._closed = False
        self._graph = _build_planner_graph()

    async def close(self):
        if self._closed:
            return
        self._closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    def _get_workspace_file_info(self, session_id: str) -> str:
        workspace_abs = SessionStore.get_workspace_path(session_id) or (
            resolve_workspace_root(session_id) if session_id else None
        )
        if not workspace_abs:
            return "No files uploaded"
        excel_info = read_workspace_excel_schema_and_sample(workspace_abs)
        try:
            return json.dumps(excel_info, ensure_ascii=False, default=str)
        except Exception:
            return excel_info.get("summary", "No files uploaded")

    async def run_flow_with_workspace(
        self, session_id: str, input_requirement: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        file_info = self._get_workspace_file_info(session_id)
        async for item in self.run_flow(
            input_requirement=input_requirement,
            file_info=file_info,
            session_id=session_id,
        ):
            yield item

    async def run_flow(
        self,
        input_requirement: str,
        file_info: str = "No files uploaded",
        session_id: str = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        运行规划流程，仅 yield：
        - state：节点开始时一条（node: analyze | decompose）
        - llm_chunk / llm_complete：LLM 输出
        - stage_result：最终规划结果
        - error：异常时
        """
        queue: asyncio.Queue = asyncio.Queue()
        final_state: Optional[Dict[str, Any]] = None
        run_error: Optional[str] = None

        async def stream_callback(event_type: str, data: Dict[str, Any]):
            await queue.put((event_type, data))

        async def run_graph():
            nonlocal final_state, run_error
            try:
                initial = {
                    "input_requirement": input_requirement,
                    "file_info": file_info,
                    "lang": self.lang,
                    "stream_callback": stream_callback,
                    "session_id": session_id,
                }
                final_state = await self._graph.ainvoke(initial)
                await queue.put(("done", None))
            except Exception as e:
                run_error = str(e)
                await queue.put(("error", {"message": run_error}))

        task = asyncio.create_task(run_graph())

        try:
            while True:
                event_type, data = await queue.get()
                if event_type == "done":
                    break
                if event_type == "error":
                    yield {"type": "error", "message": data.get("message", "未知错误")}
                    return
                if event_type == "state":
                    yield {"type": "state", "node": data.get("node", "")}
                elif event_type == "llm_chunk":
                    yield {"type": "llm_chunk", "content": data.get("content", "")}
                elif event_type == "llm_complete":
                    yield {
                        "type": "llm_complete",
                        "content": data.get("content", ""),
                        "thinking": data.get("thinking", ""),
                    }
            await task
        except asyncio.CancelledError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise

        if run_error:
            yield {"type": "error", "message": run_error}
            return

        plan_text, err, req_analysis, steps_outline = _finalize_plan_from_graph_state(final_state)
        if err:
            yield {"type": "error", "message": err}
            return
        result = {
            "输入需求": input_requirement,
            "规划全文": plan_text,
            "需求解析": req_analysis,
            "步骤分解": steps_outline,
            "执行成功": True,
            "错误信息": None,
        }

        yield {
            "type": "stage_result",
            "success": True,
            "data": result,
            "message": "规划器流程执行完成",
        }


# -------------------------- 测试 --------------------------
if __name__ == "__main__":
    async def test_planner():
        async with AgentPlanner() as planner:
            test_input = "根据 excel 各列对各行分类，生成包含分类结果的新表格。"
            async for ev in planner.run_flow(test_input, "No files uploaded"):
                if ev.get("type") == "state":
                    print(f"[状态] {ev.get('node')}")
                elif ev.get("type") == "llm_chunk":
                    print(ev.get("content", ""), end="", flush=True)
                elif ev.get("type") == "llm_complete":
                    print("\n[LLM 完成]")
                elif ev.get("type") == "stage_result":
                    print("结果:", ev.get("data", {}).keys())
                elif ev.get("type") == "error":
                    print("错误:", ev.get("message"))

    asyncio.run(test_planner())
