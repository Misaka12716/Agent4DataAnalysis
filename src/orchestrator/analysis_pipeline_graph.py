"""
顶层编排：LangGraph + LangChain 结构化 Supervisor，包裹 Planner / Coder / Worker / Reporter，
支持失败回溯（如 Worker -> Coder 修正）。子阶段事件通过 asyncio.Queue 流式上抛。
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from coder.workspace_coder import correct_and_write_code, generate_and_write_code
from planner.agent_planner import AgentPlanner
from planner.dataframe_reader import read_workspace_excel_schema_and_sample
from reporter.report_agent import stream_report
from utils.config import (
    API_KEY,
    DEFAULT_ORCHESTRATOR_MODEL,
    MAX_CODER_CORRECTIONS,
    MAX_PLANNER_RETRIES,
    MAX_SUPERVISOR_INVOCATIONS,
    OPENAI_COMPATIBLE_API_BASE,
)
from utils.workspace_manager import list_workspace_files, resolve_workspace_root
from worker.workspace_worker import run_workspace_tasks

logger = logging.getLogger(__name__)

_GRAPH_STREAM_END = object()

# LangGraph 在传递/合并 dict 状态时可能丢弃不可 JSON 序列化的值（如 asyncio.Queue），
# 因此事件队列不放入 state，而用 ContextVar 在同一次 ainvoke 调用链上传递。
_pipeline_event_queue: contextvars.ContextVar[Optional[asyncio.Queue[Any]]] = contextvars.ContextVar(
    "pipeline_event_queue", default=None
)


def _get_event_queue() -> asyncio.Queue[Any]:
    q = _pipeline_event_queue.get()
    if q is None:
        raise RuntimeError("event_queue missing in pipeline state")
    return q


class PipelineState(TypedDict, total=False):
    session_id: str
    input_data: str
    lang: str
    plan_data: Optional[Dict[str, Any]]
    planner_summary: str
    requirement_analysis: str
    steps_outline: str
    workspace_context: Dict[str, Any]
    execution_mode: str
    code_file_paths: List[str]
    coder_results: List[Dict[str, Any]]
    worker_results: Optional[Dict[str, Any]]
    supervisor_feedback: str
    last_completed_stage: str
    supervisor_invoke_count: int
    correction_attempts: int
    planner_run_count: int
    reporter_done: bool
    force_reporter: bool
    next_route: str
    last_supervisor_reason: str


class SupervisorDecision(BaseModel):
    next_stage: Literal["planner", "coder", "worker", "reporter", "finish"] = Field(
        description="下一步子阶段：planner/coder/worker/reporter，或 finish 结束"
    )
    feedback_for_next: str = Field(
        default="",
        description="给 Planner/Coder 的反馈（回溯时附上错误摘要等）",
    )
    reason: str = Field(default="", description="决策理由（简短）")


def _plan_is_valid(plan_data: Optional[Dict[str, Any]]) -> bool:
    if not plan_data:
        return False
    ra = (plan_data.get("需求解析") or "").strip()
    so = (plan_data.get("步骤分解") or "").strip()
    return bool(ra and so)


def _code_write_succeeded(coder_results: Optional[List[Dict[str, Any]]]) -> bool:
    if not coder_results:
        return False
    return any(r.get("success") for r in coder_results)


def _worker_error_text(worker_results: Optional[Dict[str, Any]]) -> str:
    if not worker_results:
        return ""
    parts: List[str] = []
    for msg in worker_results.get("error_messages") or []:
        if msg:
            parts.append(str(msg))
    for r in worker_results.get("results") or []:
        if not r.get("success") and r.get("stderr"):
            parts.append(f"{r.get('relative_path', '')}: {r.get('stderr', '')}")
    return "\n".join(parts)[:12000]


def _build_planner_summary(plan_data: Dict[str, Any], input_data: str) -> str:
    ps = (plan_data.get("规划全文") or "").strip()
    if ps:
        return ps
    return json.dumps(
        {
            "需求解析": plan_data.get("需求解析") or "",
            "步骤分解": plan_data.get("步骤分解") or "",
        },
        ensure_ascii=False,
    )


def _build_workspace_context(session_id: str) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {}
    root = resolve_workspace_root(session_id)
    if root:
        ctx["file_list"] = list_workspace_files(session_id)
        ctx["excel_schema"] = read_workspace_excel_schema_and_sample(root)
    return ctx


def _clamp_route(state: PipelineState, decision: SupervisorDecision) -> tuple[str, str]:
    """返回 (next_route, reason)，必要时钳制非法跳转。"""
    raw = decision.next_stage
    reason = (decision.reason or "").strip()
    extra = ""

    plan_ok = _plan_is_valid(state.get("plan_data"))
    code_ok = _code_write_succeeded(state.get("coder_results"))
    wr = state.get("worker_results")
    rep_done = bool(state.get("reporter_done"))
    force_rep = bool(state.get("force_reporter"))
    sup_ct = int(state.get("supervisor_invoke_count") or 0)

    if force_rep and raw not in ("reporter", "finish"):
        raw = "reporter"
        extra = "（已达 Supervisor 次数上限，强制生成报告）"

    if raw == "finish":
        if not rep_done and plan_ok and code_ok and wr is not None:
            raw = "reporter"
            extra = "（尚未产出报告，改为 reporter）"
        elif not rep_done and not plan_ok:
            raw = "planner"
            extra = "（规划无效，不能结束）"
        elif not rep_done and plan_ok and not code_ok:
            raw = "coder"
            extra = "（尚无成功代码，不能结束）"
        elif not rep_done and plan_ok and code_ok and wr is None:
            raw = "worker"
            extra = "（尚未执行，不能结束）"

    if not plan_ok and raw in ("coder", "worker", "reporter"):
        raw = "planner"
        extra = "（无有效规划，改为 planner）"

    if plan_ok and raw == "worker" and not code_ok:
        raw = "coder"
        extra = "（尚无成功写入的代码，改为 coder）"

    if plan_ok and raw == "reporter" and wr is None:
        raw = "worker" if code_ok else "coder"
        extra = "（尚未执行 worker，已钳制）"

    if rep_done and raw not in ("finish",):
        raw = "finish"
        extra = "（报告已完成，仅允许结束）"

    if sup_ct >= MAX_SUPERVISOR_INVOCATIONS and not rep_done and plan_ok and code_ok:
        if raw not in ("reporter", "finish"):
            raw = "reporter"
            extra = "（Supervisor 次数上限，强制 reporter）"

    if raw == "planner" and int(state.get("planner_run_count") or 0) >= MAX_PLANNER_RETRIES and plan_ok:
        if not code_ok:
            raw = "coder"
            extra = "（Planner 重试次数上限，改为 coder）"
        elif wr is None:
            raw = "worker"
            extra = "（Planner 重试次数上限，改为 worker）"
        else:
            raw = "reporter"
            extra = "（Planner 重试次数上限，改为 reporter）"

    if extra:
        reason = f"{reason} {extra}".strip()

    return raw, reason


def _supervisor_allowed_hint(state: PipelineState) -> str:
    last = (state.get("last_completed_stage") or "").strip()
    plan_ok = _plan_is_valid(state.get("plan_data"))
    code_ok = _code_write_succeeded(state.get("coder_results"))
    wr = state.get("worker_results")
    rep_done = bool(state.get("reporter_done"))
    wfail = wr is not None and not wr.get("success", False)

    lines = [
        f"last_completed_stage={last or '(无)'}",
        f"plan_ok={plan_ok} code_ok={code_ok} worker_ran={wr is not None} worker_success={wr.get('success') if wr else None}",
        f"reporter_done={rep_done} correction_attempts={state.get('correction_attempts', 0)}",
    ]
    if wfail:
        lines.append(
            "Worker 未成功：通常应选择 coder，并在 feedback_for_next 中粘贴 stderr/关键错误。"
        )
    if not plan_ok:
        lines.append("当前无有效规划：应选择 planner。")
    elif last == "planner" and plan_ok:
        lines.append("规划刚完成：通常应选择 coder。")
    elif last == "coder" and code_ok:
        lines.append("代码已写入：通常应选择 worker。")
    elif last == "worker" and wr and wr.get("success"):
        lines.append("执行成功：通常应选择 reporter。")
    elif last == "reporter" and rep_done:
        lines.append("报告已流式完成：必须选择 finish。")
    return "\n".join(lines)


async def _supervisor_node(state: PipelineState) -> Dict[str, Any]:
    q = _get_event_queue()

    n = int(state.get("supervisor_invoke_count") or 0) + 1
    force_rep = n >= MAX_SUPERVISOR_INVOCATIONS

    lang = state.get("lang") or "zh"
    hint = _supervisor_allowed_hint(state)
    payload = {
        "用户原始需求": state.get("input_data", ""),
        "编排反馈": (state.get("supervisor_feedback") or "").strip(),
        "状态摘要": hint,
        "worker_error_excerpt": _worker_error_text(state.get("worker_results")),
    }

    sys_zh = """你是数据分析流水线的编排 Supervisor。根据「状态摘要」与可选错误摘录，选择下一步子阶段。
规则要点：
- 仅输出结构化字段；next_stage 必须是 planner、coder、worker、reporter、finish 之一。
- 若无有效规划（plan_ok=false），必须选 planner。
- 若规划有效但尚未有成功写入代码（code_ok=false），应选 coder。
- 若代码已写入但未执行 worker，应选 worker（不要跳步到 reporter）。
- 若 worker 失败，应优先选 coder 进行修正，并在 feedback_for_next 中写明错误与期望。
- 若 worker 成功且尚未报告，应选 reporter。
- 若 reporter_done=true，只能选 finish。
- feedback_for_next 会在进入 planner 时附加到用户需求的末尾；进入 coder 时可作为额外上下文（当前实现主要依赖 worker 失败时的修正提示）。"""
    sys_en = """You supervise Planner->Coder->Worker->Reporter. Choose next_stage based on state summary. Same rules as Chinese version."""
    system = sys_zh if lang == "zh" else sys_en
    user = json.dumps(payload, ensure_ascii=False)

    llm = ChatOpenAI(
        model=DEFAULT_ORCHESTRATOR_MODEL,
        temperature=0.1,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
    )
    structured = llm.with_structured_output(SupervisorDecision)
    try:
        decision: SupervisorDecision = await structured.ainvoke(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
    except Exception as e:
        logger.exception("supervisor structured invoke failed: %s", e)
        decision = SupervisorDecision(
            next_stage="reporter" if _plan_is_valid(state.get("plan_data")) and _code_write_succeeded(state.get("coder_results")) else "planner",
            feedback_for_next="",
            reason=f"Supervisor LLM 异常，已回退：{e}",
        )

    next_route, reason = _clamp_route({**state, "supervisor_invoke_count": n, "force_reporter": force_rep}, decision)
    feedback = (decision.feedback_for_next or "").strip()
    if force_rep and next_route == "reporter":
        pass

    orch = {
        "type": "orchestrator",
        "data": {
            "next": next_route,
            "reason": reason,
            "feedback": feedback,
            "supervisor_invoke": n,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    await q.put(orch)

    return {
        "supervisor_invoke_count": n,
        "next_route": next_route,
        "last_supervisor_reason": reason,
        "supervisor_feedback": feedback,
        "force_reporter": force_rep and not state.get("reporter_done"),
    }


async def _planner_node(state: PipelineState) -> Dict[str, Any]:
    q = _get_event_queue()
    session_id = state["session_id"]
    lang = state.get("lang") or "zh"
    base_input = (state.get("input_data") or "").strip()
    fb = (state.get("supervisor_feedback") or "").strip()
    if fb:
        if lang == "zh":
            input_req = f"{base_input}\n\n【编排者反馈】\n{fb}"
        else:
            input_req = f"{base_input}\n\n[Orchestrator feedback]\n{fb}"
    else:
        input_req = base_input

    plan_data: Optional[Dict[str, Any]] = None
    async with AgentPlanner() as planner:
        planner.lang = lang
        async for event in planner.run_flow_with_workspace(session_id, input_req):
            await q.put({"type": "planner", "data": event})
            if event.get("type") == "stage_result" and event.get("data"):
                plan_data = event["data"]

    if not _plan_is_valid(plan_data):
        return {
            "plan_data": plan_data,
            "planner_summary": "",
            "requirement_analysis": "",
            "steps_outline": "",
            "last_completed_stage": "planner",
            "supervisor_feedback": "",
            "planner_run_count": int(state.get("planner_run_count") or 0) + 1,
            # 新规划后丢弃旧执行结果，避免 coder 误判需 correct
            "worker_results": None,
        }

    assert plan_data is not None
    ra = (plan_data.get("需求解析") or "").strip()
    so = (plan_data.get("步骤分解") or "").strip()
    ps = _build_planner_summary(plan_data, base_input)
    return {
        "plan_data": plan_data,
        "planner_summary": ps,
        "requirement_analysis": ra,
        "steps_outline": so,
        "workspace_context": _build_workspace_context(session_id),
        "last_completed_stage": "planner",
        "supervisor_feedback": "",
        "planner_run_count": int(state.get("planner_run_count") or 0) + 1,
        "worker_results": None,
    }


async def _coder_node(state: PipelineState) -> Dict[str, Any]:
    q = _get_event_queue()
    session_id = state["session_id"]
    lang = state.get("lang") or "zh"
    paths = state.get("code_file_paths") or ["main.py"]
    rel = paths[0]
    ws = state.get("workspace_context") or _build_workspace_context(session_id)
    wr = state.get("worker_results")
    fb = (state.get("supervisor_feedback") or "").strip()
    use_correct = (
        wr is not None
        and not wr.get("success", False)
        and _code_write_succeeded(state.get("coder_results"))
        and int(state.get("correction_attempts") or 0) < MAX_CODER_CORRECTIONS
    )
    err_text = _worker_error_text(wr) if use_correct else ""
    if fb and use_correct:
        err_text = f"{fb}\n\n{err_text}".strip() if err_text else fb

    if use_correct:
        one = correct_and_write_code(
            session_id,
            rel,
            err_text,
            lang=lang,
            workspace_context=ws,
        )
        results = [one]
        corr = int(state.get("correction_attempts") or 0) + 1
    else:
        planner_summary = (state.get("planner_summary") or "").strip() or (state.get("input_data") or "")
        code_specs = [
            {
                "task_desc": planner_summary,
                "requirement_analysis": state.get("requirement_analysis") or "",
                "steps_outline": state.get("steps_outline") or "",
                "relative_path": rel,
            }
        ]
        if fb and not use_correct:
            code_specs[0]["task_desc"] = f"{planner_summary}\n\n【编排者反馈】\n{fb}"
        results = generate_and_write_code(
            session_id,
            code_specs,
            lang=lang,
            workspace_context=ws,
        )
        corr = int(state.get("correction_attempts") or 0)

    await q.put({"type": "coder", "data": results})
    return {
        "coder_results": results,
        "workspace_context": ws,
        "code_file_paths": paths,
        "correction_attempts": corr,
        "last_completed_stage": "coder",
        "supervisor_feedback": "",
    }


async def _worker_node(state: PipelineState) -> Dict[str, Any]:
    q = _get_event_queue()
    session_id = state["session_id"]
    mode = state.get("execution_mode") or "simple"
    paths = state.get("code_file_paths") or ["main.py"]
    results = run_workspace_tasks(session_id, mode, paths)
    await q.put({"type": "worker", "data": results})
    return {
        "worker_results": results,
        "last_completed_stage": "worker",
        "supervisor_feedback": "",
    }


async def _reporter_node(state: PipelineState) -> Dict[str, Any]:
    q = _get_event_queue()
    session_id = state["session_id"]
    lang = state.get("lang") or "zh"
    summary = (state.get("planner_summary") or "").strip() or (state.get("input_data") or "")
    wr = state.get("worker_results") or {
        "success": False,
        "results": [],
        "logs": "",
        "error_messages": ["尚未执行 Worker"],
    }
    async for chunk in stream_report(
        summary,
        wr,
        lang=lang,
        session_id=session_id,
    ):
        await q.put({"type": "report_chunk", "content": chunk})
    return {
        "reporter_done": True,
        "last_completed_stage": "reporter",
        "supervisor_feedback": "",
    }


def _route_from_supervisor(state: PipelineState) -> str:
    return state.get("next_route") or "planner"


def _build_pipeline_graph():
    # 必须用带注解的 TypedDict：StateGraph(dict) 会把整份 state 放在单一 __root__ 通道上，
    # 节点若只返回部分键会整包替换 state，导致下一节点缺少 session_id 等字段。
    g = StateGraph(PipelineState)
    g.add_node("supervisor", _supervisor_node)
    g.add_node("planner", _planner_node)
    g.add_node("coder", _coder_node)
    g.add_node("worker", _worker_node)
    g.add_node("reporter", _reporter_node)
    g.set_entry_point("supervisor")
    g.add_conditional_edges(
        "supervisor",
        _route_from_supervisor,
        {
            "planner": "planner",
            "coder": "coder",
            "worker": "worker",
            "reporter": "reporter",
            "finish": END,
        },
    )
    g.add_edge("planner", "supervisor")
    g.add_edge("coder", "supervisor")
    g.add_edge("worker", "supervisor")
    g.add_edge("reporter", "supervisor")
    return g.compile()


_compiled_pipeline = None


def get_pipeline_graph():
    global _compiled_pipeline
    if _compiled_pipeline is None:
        _compiled_pipeline = _build_pipeline_graph()
    return _compiled_pipeline


async def run_orchestrated_analysis_stream(
    session_id: str,
    input_data: str,
    lang: str = "zh",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    运行可回溯编排流水线，yield 与原先 analysis_stream 兼容的 dict（外加 type=orchestrator）。
    """
    q: asyncio.Queue[Any] = asyncio.Queue()
    initial: PipelineState = {
        "session_id": session_id,
        "input_data": input_data,
        "lang": lang,
        "execution_mode": "simple",
        "code_file_paths": ["main.py"],
        "supervisor_feedback": "",
        "supervisor_invoke_count": 0,
        "correction_attempts": 0,
        "planner_run_count": 0,
        "reporter_done": False,
        "force_reporter": False,
        "coder_results": [],
        "workspace_context": {},
    }

    graph = get_pipeline_graph()
    run_error: Optional[str] = None
    final_state: Optional[Dict[str, Any]] = None

    async def _invoke():
        nonlocal run_error, final_state
        try:
            final_state = await graph.ainvoke(initial)
        except Exception as e:
            logger.exception("pipeline graph failed: %s", e)
            run_error = str(e)
        finally:
            await q.put(_GRAPH_STREAM_END)

    token = _pipeline_event_queue.set(q)
    try:
        task = asyncio.create_task(_invoke())
        try:
            while True:
                item = await q.get()
                if item is _GRAPH_STREAM_END:
                    break
                yield item
            await task
        except asyncio.CancelledError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise
    finally:
        _pipeline_event_queue.reset(token)

    if run_error:
        yield {
            "type": "streaming_error",
            "error": run_error,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    elif final_state and not final_state.get("reporter_done"):
        yield {
            "type": "error",
            "message": "流水线未正常完成报告阶段（可能规划/代码多次失败或达到上限）",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
