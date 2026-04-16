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

from json_repair import loads as json_repair_loads
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from coder.workspace_coder import correct_and_write_code, generate_and_write_code
from planner.agent_planner import AgentPlanner
from planner.dataframe_reader import read_workspace_excel_schema_and_sample
from reporter.report_agent import stream_report
from configs.prompts import append_orchestrator_feedback, get_system_prompt
from configs.config import (
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


def _should_try_json_schema_method(llm: ChatOpenAI) -> bool:
    """
    部分 OpenAI 兼容网关/本地模型不支持 response_format=json_schema，
    会返回 500（例如 vocabulary/format 相关报错）。对这些模型直接跳过 json_schema。
    """
    model_name = (
        str(getattr(llm, "model_name", "") or getattr(llm, "model", "") or "")
        .strip()
        .lower()
    )
    if not model_name:
        return True

    # 常见本地/兼容模型：优先 function_calling + raw fallback，避免 json_schema 触发网关 500。
    unsupported_keywords = (
        "qwen",
        "llama",
        "mistral",
        "deepseek",
        "gemma",
        "yi",
    )
    return not any(k in model_name for k in unsupported_keywords)

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
    # 该状态由 LangGraph 在节点间传递；采用 total=False 允许节点仅返回增量字段。
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
        max_length=8000,
        description="给 Planner/Coder 的反馈（回溯时附上错误摘要等）",
    )
    reason: str = Field(
        default="",
        max_length=2000,
        description="决策理由（简短）",
    )


def _plan_is_valid(plan_data: Optional[Dict[str, Any]]) -> bool:
    # Planner 的最小有效产物：必须同时包含“需求解析”和“步骤分解”。
    if not plan_data:
        return False
    ra = (plan_data.get("需求解析") or "").strip()
    so = (plan_data.get("步骤分解") or "").strip()
    return bool(ra and so)


def _code_write_succeeded(coder_results: Optional[List[Dict[str, Any]]]) -> bool:
    # 只要任一目标文件写入成功，就允许继续到 Worker 执行验证。
    if not coder_results:
        return False
    return any(r.get("success") for r in coder_results)


def _worker_error_text(worker_results: Optional[Dict[str, Any]]) -> str:
    # 汇总 Worker 错误，作为下一轮 Coder 修复输入（避免传入过长日志）。
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
    # 只注入对生成代码有价值的上下文，减少提示词冗余。
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
    # 给 Supervisor 的“软约束提示”，用于降低不合理路由概率。
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


def _stringify_llm_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            else:
                parts.append(str(p))
        return "".join(parts)
    return str(content) if content is not None else ""


def _parse_supervisor_decision_from_content(content: str) -> Optional[SupervisorDecision]:
    """
    在 json_schema / response_format 解析失败时，从原始 assistant 文本中尽量恢复 JSON。
    兼容部分网关把推理标签拼进 content、或未闭合字符串等情况（借助 json_repair）。
    """
    t = (content or "").strip()
    if not t:
        return None
    if "```" in t:
        for block in t.split("```"):
            b = block.strip()
            if b.lower().startswith("json"):
                b = b[4:].lstrip()
            if b.startswith("{"):
                t = b
                break
    start = t.find("{")
    if start < 0:
        return None
    tail = t[start:]
    end = tail.rfind("}")
    candidate = tail[: end + 1] if end >= 0 else tail
    try:
        data = json_repair_loads(candidate)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return SupervisorDecision.model_validate(data)
    except Exception:
        return None


async def _supervisor_llm_decision(
    llm: ChatOpenAI,
    messages: List[Dict[str, str]],
) -> SupervisorDecision:
    """优先 tool/function 参数承载结构，避免部分模型在 message.content 里混入非 JSON 片段。"""
    last_err: Optional[BaseException] = None
    # 优先使用结构化输出；失败后再退回 raw content 修复解析。
    methods: List[str] = ["function_calling"]
    if _should_try_json_schema_method(llm):
        methods.append("json_schema")
    for method in methods:
        structured = llm.with_structured_output(SupervisorDecision, method=method)
        try:
            out = await structured.ainvoke(messages)
            if isinstance(out, SupervisorDecision):
                return out
            if isinstance(out, dict):
                return SupervisorDecision.model_validate(out)
        except Exception as e:
            last_err = e
            logger.warning("supervisor structured_output(%s) failed: %s", method, e)

    try:
        raw = await llm.ainvoke(messages)
        text = _stringify_llm_content(getattr(raw, "content", ""))
        parsed = _parse_supervisor_decision_from_content(text)
        if parsed is not None:
            logger.info("supervisor recovered decision from raw assistant content")
            return parsed
    except Exception as e:
        last_err = e
        logger.warning("supervisor raw ainvoke fallback failed: %s", e)

    assert last_err is not None
    raise last_err


async def _supervisor_node(state: PipelineState) -> Dict[str, Any]:
    # Supervisor 只负责“路由决策 + 反馈”，不直接执行业务阶段。
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

    system = get_system_prompt("supervisor", lang)
    user = json.dumps(payload, ensure_ascii=False)

    llm = ChatOpenAI(
        model=DEFAULT_ORCHESTRATOR_MODEL,
        temperature=0.1,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        decision = await _supervisor_llm_decision(llm, messages)
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
    # Planner 支持接收 Supervisor 反馈，重新产出规划。
    q = _get_event_queue()
    session_id = state["session_id"]
    lang = state.get("lang") or "zh"
    base_input = (state.get("input_data") or "").strip()
    fb = (state.get("supervisor_feedback") or "").strip()
    input_req = append_orchestrator_feedback(base_input, fb, lang)

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
    # Coder 在“首次生成”和“基于 Worker 错误修复”两条路径间切换。
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
            code_specs[0]["task_desc"] = append_orchestrator_feedback(planner_summary, fb, lang)
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
    # Worker 负责执行代码并产出可被 Supervisor/Coder 消费的结构化结果。
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
    # Reporter 流式产出自然语言报告，不改变规划/执行结果本身。
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
    # LangGraph 条件边路由函数；默认回落到 planner。
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
    # 编译图开销较高，按进程级单例缓存。
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

    async def _invoke():
        terminal_event: Optional[Dict[str, Any]] = None
        try:
            # 实际图执行在后台任务中进行；前台通过队列持续消费事件。
            final_state = await graph.ainvoke(initial)
            if final_state and not final_state.get("reporter_done"):
                terminal_event = {
                    "type": "error",
                    "message": "流水线未正常完成报告阶段（可能规划/代码多次失败或达到上限）",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
        except Exception as e:
            logger.exception("pipeline graph failed: %s", e)
            terminal_event = {
                "type": "streaming_error",
                "error": str(e),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        finally:
            # 终态事件与普通阶段事件统一走队列转发，避免依赖函数尾部二次 yield。
            if terminal_event is not None:
                await q.put(terminal_event)
            await q.put(_GRAPH_STREAM_END)

    token = _pipeline_event_queue.set(q)
    try:
        task = asyncio.create_task(_invoke())
        try:
            # 统一从队列转发节点事件，维持与旧 streaming 协议兼容。
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
