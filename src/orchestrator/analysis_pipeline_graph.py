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
from db.session_store import SessionStore
from utils.workspace_manager import list_workspace_files, resolve_workspace_root
from utils.model_logger import log_phase_end, log_phase_start
from utils.session_memory import (
    format_memory_for_prompt,
    persist_from_pipeline_state,
    read_session_memory_for_prompt,
)
from worker.workspace_worker import run_workspace_tasks

logger = logging.getLogger(__name__)

# 用于通知流式消费者“图执行已结束”的哨兵对象。
# 之所以不用 None，是为了避免和真实事件 payload（可能为 None）混淆。
_GRAPH_STREAM_END = object()


def _persist_pipeline_event(session_id: str, payload: Dict[str, Any]) -> None:
    """将编排事件增量持久化到会话内容，避免依赖 SSE 连接状态。"""
    try:
        SessionStore.append_content(
            session_id,
            json.dumps(payload, ensure_ascii=False) + "\n",
        )
    except Exception:
        logger.exception("persist pipeline event failed: session_id=%s", session_id)


async def _emit_pipeline_event(
    session_id: str,
    q: asyncio.Queue[Any],
    payload: Dict[str, Any],
) -> None:
    """事件先入库，再进入流式队列。"""
    _persist_pipeline_event(session_id, payload)
    await q.put(payload)


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
    """
    从 ContextVar 获取当前执行链路绑定的事件队列。

    该函数只应在 run_orchestrated_analysis_stream() 创建的调用栈内使用；
    若脱离该上下文调用，会抛出 RuntimeError，提示上层缺失初始化步骤。
    """
    q = _pipeline_event_queue.get()
    if q is None:
        raise RuntimeError("event_queue missing in pipeline state")
    return q


class PipelineState(TypedDict, total=False):
    """
    LangGraph 在节点间流转的共享状态。

    设计要点：
    - 使用 total=False：允许节点只返回“增量更新”的字段，未返回字段由图框架保留。
    - 字段按“输入 -> 规划 -> 代码 -> 执行 -> 报告 -> 编排控制”分层，便于路由决策。
    """
    # 会话与基础输入
    session_id: str
    input_data: str
    lang: str
    # Planner 产物
    plan_data: Optional[Dict[str, Any]]
    planner_summary: str
    requirement_analysis: str
    steps_outline: str
    # 与代码生成/执行相关的上下文和结果
    workspace_context: Dict[str, Any]
    execution_mode: str
    code_file_paths: List[str]
    coder_results: List[Dict[str, Any]]
    worker_results: Optional[Dict[str, Any]]
    # Supervisor 控制信息
    supervisor_feedback: str
    last_completed_stage: str
    supervisor_invoke_count: int
    correction_attempts: int
    planner_run_count: int
    reporter_done: bool
    force_reporter: bool
    next_route: str
    last_supervisor_reason: str
    # 会话记忆（SESSION_MEMORY.md）：编排轨迹与最近一次 Coder 模式
    memory_trace: List[Dict[str, Any]]
    last_coder_mode: str


class SupervisorDecision(BaseModel):
    """Supervisor LLM 的结构化输出契约。"""
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
    """
    生成给后续 Coder/Reporter 使用的规划摘要文本。

    优先取 Planner 返回的“规划全文”；若缺失则回退为精简 JSON（需求解析 + 步骤分解）。
    """
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
    """
    对 Supervisor 的路由决策做安全钳制，返回 (next_route, reason)。

    背景：
    - LLM 可能给出“语义上合理但状态上非法”的跳转（例如未执行 Worker 就 finish）。
    - 该函数作为最终守门员，保证流水线状态机满足最小前置条件。

    主要规则：
    - 未完成报告前，finish 需满足 plan_ok + code_ok + worker 已运行，否则回退到缺失阶段。
    - 无有效规划时，任何后续阶段都回退到 planner。
    - 未有成功代码写入时，不允许直接进入 worker/reporter。
    - 达到 Supervisor/Planner 重试上限时，优先推进到可收敛阶段，避免死循环。
    """
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
    # 注意：这是提示而非硬约束，最终仍由 _clamp_route() 保底校验。
    last = (state.get("last_completed_stage") or "").strip()
    plan_ok = _plan_is_valid(state.get("plan_data"))
    code_ok = _code_write_succeeded(state.get("coder_results"))
    wr = state.get("worker_results")
    rep_done = bool(state.get("reporter_done"))
    wfail = wr is not None and not wr.get("success", False)
    lang = (state.get("lang") or "zh").strip().lower()
    role_line = (
        "Role split: Coder stdout = verifiable stats/facts only; Reporter writes narrative conclusions and recommendations from logs—avoid duplication."
        if lang == "en"
        else "职责边界：Coder 脚本只应输出统计/事实类结果（stdout）；叙述性结论与建议由 Reporter 根据日志撰写，二者勿重复。"
    )

    lines = [
        f"last_completed_stage={last or '(无)'}",
        f"plan_ok={plan_ok} code_ok={code_ok} worker_ran={wr is not None} worker_success={wr.get('success') if wr else None}",
        f"reporter_done={rep_done} correction_attempts={state.get('correction_attempts', 0)}",
        role_line,
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
    """
    将不同模型返回的 content 统一折叠为纯文本字符串。

    兼容场景：
    - 标准字符串内容
    - 多段列表内容（字符串块、{"text": "..."} 块等）
    - 其他对象（降级为 str()）
    """
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
    """
    让 Supervisor 以“结构化优先、文本修复兜底”方式输出决策。

    调用策略：
    1) 先尝试 function_calling（通常兼容性更好）
    2) 对可能支持的模型再尝试 json_schema
    3) 若都失败，回落到 raw content，并通过 json_repair 恢复 JSON

    这样可以在多种 OpenAI 兼容网关/模型下提升鲁棒性。
    """
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
    session_id = state["session_id"]

    # 先自增计数，再据此判断是否触发强制 reporter。
    n = int(state.get("supervisor_invoke_count") or 0) + 1
    log_phase_start(
        session_id,
        "supervisor",
        {
            "invoke": n,
            "last_completed_stage": state.get("last_completed_stage") or "",
            "planner_run_count": int(state.get("planner_run_count") or 0),
            "correction_attempts": int(state.get("correction_attempts") or 0),
        },
    )
    force_rep = n >= MAX_SUPERVISOR_INVOCATIONS

    lang = state.get("lang") or "zh"
    hint = _supervisor_allowed_hint(state)
    # 将“用户输入 + 过程反馈 + 执行错误摘要”合并为监督决策上下文。
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

    # 关键：任何 LLM 决策都必须经过钳制，确保状态机合法推进。
    next_route, reason = _clamp_route({**state, "supervisor_invoke_count": n, "force_reporter": force_rep}, decision)
    feedback = (decision.feedback_for_next or "").strip()
    if force_rep and next_route == "reporter":
        pass

    mem_hint = format_memory_for_prompt(read_session_memory_for_prompt(session_id), lang).strip()
    orch = {
        "type": "orchestrator",
        "data": {
            "next": next_route,
            "reason": reason,
            "feedback": feedback,
            "supervisor_invoke": n,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **({"session_memory": mem_hint} if mem_hint else {}),
        },
    }
    await _emit_pipeline_event(session_id, q, orch)

    log_phase_end(
        session_id,
        "supervisor",
        {
            "invoke": n,
            "next_route": next_route,
            "reason": reason,
            "feedback_len": len(feedback),
            "force_reporter": bool(force_rep and not state.get("reporter_done")),
        },
    )

    prev_trace = list(state.get("memory_trace") or [])
    new_trace = (
        prev_trace
        + [
            {
                "next": next_route,
                "reason": reason,
                "feedback": (feedback or "")[:800],
                "invoke": n,
            }
        ]
    )[-8:]
    sup_out = {
        "supervisor_invoke_count": n,
        "next_route": next_route,
        "last_supervisor_reason": reason,
        "supervisor_feedback": feedback,
        "force_reporter": force_rep and not state.get("reporter_done"),
        "memory_trace": new_trace,
    }
    try:
        persist_from_pipeline_state(
            {**state, **sup_out},
            last_event="supervisor",
            streaming_status="running",
            pipeline_note="本轮分析进行中：Supervisor 已决策下一步。",
        )
    except Exception:
        logger.exception("session memory persist after supervisor failed")
    return sup_out


async def _planner_node(state: PipelineState) -> Dict[str, Any]:
    # Planner 支持接收 Supervisor 反馈，重新产出规划。
    q = _get_event_queue()
    session_id = state["session_id"]
    lang = state.get("lang") or "zh"
    base_input = (state.get("input_data") or "").strip()
    fb = (state.get("supervisor_feedback") or "").strip()
    input_req = append_orchestrator_feedback(base_input, fb, lang)
    next_run = int(state.get("planner_run_count") or 0) + 1
    log_phase_start(
        session_id,
        "planner",
        {
            "planner_run": next_run,
            "has_supervisor_feedback": bool(fb),
            "input_chars": len(input_req),
        },
    )

    plan_data: Optional[Dict[str, Any]] = None
    # 透传 Planner 的流式事件，保持前端可观测性。
    async with AgentPlanner() as planner:
        planner.lang = lang
        async for event in planner.run_flow_with_workspace(session_id, input_req):
            await _emit_pipeline_event(session_id, q, {"type": "planner", "data": event})
            if event.get("type") == "stage_result" and event.get("data"):
                plan_data = event["data"]

    # 规划无效时只记录结果，不提前抛错；交由 Supervisor 继续路由（通常会重试 planner）。
    if not _plan_is_valid(plan_data):
        log_phase_end(
            session_id,
            "planner",
            {
                "planner_run": next_run,
                "plan_valid": False,
                "planner_summary_len": 0,
            },
        )
        bad = {
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
        try:
            persist_from_pipeline_state(
                {**state, **bad},
                last_event="planner",
                streaming_status="running",
                pipeline_note="Planner 已运行；当前规划尚未通过校验。",
            )
        except Exception:
            logger.exception("session memory persist after planner (invalid) failed")
        return bad

    assert plan_data is not None
    ra = (plan_data.get("需求解析") or "").strip()
    so = (plan_data.get("步骤分解") or "").strip()
    ps = _build_planner_summary(plan_data, base_input)
    log_phase_end(
        session_id,
        "planner",
        {
            "planner_run": next_run,
            "plan_valid": True,
            "planner_summary_len": len(ps),
            "requirement_analysis_len": len(ra),
            "steps_outline_len": len(so),
        },
    )
    good = {
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
    try:
        persist_from_pipeline_state(
            {**state, **good},
            last_event="planner",
            streaming_status="running",
            pipeline_note="Planner 已产出有效规划；工作区上下文已刷新。",
        )
    except Exception:
        logger.exception("session memory persist after planner (valid) failed")
    return good


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
    # 是否进入“修复模式”：
    # 1) Worker 已运行且失败
    # 2) 之前至少有一次成功写入（说明文件已存在可修补）
    # 3) 未超过最大修复次数
    use_correct = (
        wr is not None
        and not wr.get("success", False)
        and _code_write_succeeded(state.get("coder_results"))
        and int(state.get("correction_attempts") or 0) < MAX_CODER_CORRECTIONS
    )
    err_text = _worker_error_text(wr) if use_correct else ""
    if fb and use_correct:
        err_text = f"{fb}\n\n{err_text}".strip() if err_text else fb

    log_phase_start(
        session_id,
        "coder",
        {
            "relative_path": rel,
            "mode": "correct" if use_correct else "generate",
            "correction_attempts_before": int(state.get("correction_attempts") or 0),
        },
    )

    mem_ex = read_session_memory_for_prompt(session_id)

    if use_correct:
        # 修复路径：把错误摘要喂给 Coder，定点修补目标文件。
        one = correct_and_write_code(
            session_id,
            rel,
            err_text,
            lang=lang,
            workspace_context=ws,
            session_memory_excerpt=mem_ex,
        )
        results = [one]
        corr = int(state.get("correction_attempts") or 0) + 1
    else:
        # 生成路径：基于 Planner 摘要首次生成代码文件。
        planner_summary = (state.get("planner_summary") or "").strip() or (state.get("input_data") or "")
        steps_outline = (state.get("steps_outline") or "").strip()
        task_desc = planner_summary
        # 有独立「需求解析/步骤分解」时，_generate_code_for_task 以二者为主，task_desc 仅作回退；编排反馈须写入步骤分解（及回退用的 task_desc），否则 Coder 收不到。
        if fb and not use_correct:
            steps_outline = append_orchestrator_feedback(steps_outline, fb, lang)
            task_desc = append_orchestrator_feedback(planner_summary, fb, lang)
        code_specs = [
            {
                "task_desc": task_desc,
                "requirement_analysis": state.get("requirement_analysis") or "",
                "steps_outline": steps_outline,
                "relative_path": rel,
            }
        ]
        results = generate_and_write_code(
            session_id,
            code_specs,
            lang=lang,
            workspace_context=ws,
            session_memory_excerpt=mem_ex,
        )
        corr = int(state.get("correction_attempts") or 0)

    await _emit_pipeline_event(session_id, q, {"type": "coder", "data": results})
    log_phase_end(
        session_id,
        "coder",
        {
            "relative_path": rel,
            "mode": "correct" if use_correct else "generate",
            "results": [
                {
                    "relative_path": r.get("relative_path"),
                    "success": r.get("success"),
                    "error": (r.get("error") or "")[:500],
                }
                for r in results
            ],
            "correction_attempts_after": corr,
        },
    )
    mode_label = "correct" if use_correct else "generate"
    cod_out = {
        "coder_results": results,
        "workspace_context": ws,
        "code_file_paths": paths,
        "correction_attempts": corr,
        "last_completed_stage": "coder",
        "supervisor_feedback": "",
        "last_coder_mode": mode_label,
    }
    try:
        persist_from_pipeline_state(
            {**state, **cod_out},
            last_event="coder",
            streaming_status="running",
            pipeline_note="Coder 已完成写入或修正；详见代码与修正章节。",
        )
    except Exception:
        logger.exception("session memory persist after coder failed")
    return cod_out


async def _worker_node(state: PipelineState) -> Dict[str, Any]:
    # Worker 负责执行代码并产出可被 Supervisor/Coder 消费的结构化结果。
    q = _get_event_queue()
    session_id = state["session_id"]
    mode = state.get("execution_mode") or "simple"
    paths = state.get("code_file_paths") or ["main.py"]
    log_phase_start(
        session_id,
        "worker",
        {"execution_mode": mode, "paths": paths},
    )
    # run_workspace_tasks 为同步函数，这里直接调用即可；
    # 若后续执行耗时显著，可考虑迁移到线程池。
    results = run_workspace_tasks(session_id, mode, paths)
    await _emit_pipeline_event(session_id, q, {"type": "worker", "data": results})
    log_phase_end(
        session_id,
        "worker",
        {
            "success": bool(results.get("success")),
            "error_messages_count": len(results.get("error_messages") or []),
        },
    )
    wr_out = {
        "worker_results": results,
        "last_completed_stage": "worker",
        "supervisor_feedback": "",
    }
    try:
        persist_from_pipeline_state(
            {**state, **wr_out},
            last_event="worker",
            streaming_status="running",
            pipeline_note="Worker 已执行；错误与 stdout 摘要见记忆文件。",
        )
    except Exception:
        logger.exception("session memory persist after worker failed")
    return wr_out


async def _reporter_node(state: PipelineState) -> Dict[str, Any]:
    # Reporter 流式产出自然语言报告，不改变规划/执行结果本身。
    q = _get_event_queue()
    session_id = state["session_id"]
    lang = state.get("lang") or "zh"
    summary = (state.get("planner_summary") or "").strip() or (state.get("input_data") or "")
    # 若无 Worker 结果，构造兜底结构，确保 Reporter 输入契约稳定。
    wr = state.get("worker_results") or {
        "success": False,
        "results": [],
        "logs": "",
        "error_messages": ["尚未执行 Worker"],
    }
    log_phase_start(
        session_id,
        "reporter",
        {
            "planner_summary_chars": len(summary),
            "worker_success": bool(wr.get("success")),
            "fallback_worker": state.get("worker_results") is None,
        },
    )
    report_parts: List[str] = []
    async for chunk in stream_report(
        summary,
        wr,
        lang=lang,
        session_id=session_id,
    ):
        if chunk:
            report_parts.append(chunk)
        await _emit_pipeline_event(session_id, q, {"type": "report_chunk", "content": chunk})
    log_phase_end(session_id, "reporter", {"stream_finished": True})
    report_excerpt = ("".join(report_parts))[:3000]
    rep_out = {
        "reporter_done": True,
        "last_completed_stage": "reporter",
        "supervisor_feedback": "",
    }
    try:
        persist_from_pipeline_state(
            {**state, **rep_out},
            report_excerpt=report_excerpt,
            last_event="reporter",
            streaming_status="running",
            pipeline_note="Reporter 已流式完成；报告摘录已写入记忆。",
        )
    except Exception:
        logger.exception("session memory persist after reporter failed")
    return rep_out


def _route_from_supervisor(state: PipelineState) -> str:
    # LangGraph 条件边路由函数；默认回落到 planner。
    return state.get("next_route") or "planner"


def _build_pipeline_graph():
    # 必须用带注解的 TypedDict：StateGraph(dict) 会把整份 state 放在单一 __root__ 通道上，
    # 节点若只返回部分键会整包替换 state，导致下一节点缺少 session_id 等字段。
    g = StateGraph(PipelineState)
    # 节点均为“纯函数式增量更新”：读取 state，返回部分字段覆盖。
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

    运行模型：
    - 背景任务执行 LangGraph（_invoke）
    - 当前协程持续消费 asyncio.Queue 并向调用方 yield
    - 所有节点事件、异常终态事件都统一经由队列转发
    - 通过 ContextVar 将队列注入节点执行上下文，避免 state 序列化问题
    """
    q: asyncio.Queue[Any] = asyncio.Queue()
    # 初始状态：仅填充编排最小闭环所需字段，其余由节点逐步补全。
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
        "memory_trace": [],
        "last_coder_mode": "",
    }

    log_phase_start(
        session_id,
        "orchestrated_pipeline",
        {"lang": lang, "input_chars": len(input_data or "")},
    )

    graph = get_pipeline_graph()

    async def _invoke():
        terminal_event: Optional[Dict[str, Any]] = None
        final_state: Optional[Dict[str, Any]] = None
        try:
            try:
                persist_from_pipeline_state(
                    {
                        **initial,
                        "workspace_context": _build_workspace_context(session_id),
                    },
                    last_event="pipeline_start",
                    streaming_status="running",
                    pipeline_note="本轮分析任务已启动；以下为启动时工作区快照。",
                )
            except Exception:
                logger.exception("session memory persist at pipeline_start failed")
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
            try:
                merged = {**initial, **(final_state or {})}
                if terminal_event is None:
                    persist_from_pipeline_state(
                        merged,
                        streaming_status="completed",
                        last_event="pipeline_finished",
                        pipeline_note="本轮流水线已结束（正常路径）。",
                    )
                else:
                    persist_from_pipeline_state(
                        merged,
                        streaming_status="error_or_incomplete",
                        last_event="pipeline_finished",
                        pipeline_note="流水线已结束（异常、中断或未走到 Reporter）。",
                    )
            except Exception:
                logger.exception("session memory persist at pipeline terminal failed")
            log_phase_end(
                session_id,
                "orchestrated_pipeline",
                {
                    "reporter_done": bool(final_state and final_state.get("reporter_done")),
                    "terminal_event": terminal_event,
                },
            )
            # 终态事件与普通阶段事件统一走队列转发，避免依赖函数尾部二次 yield。
            if terminal_event is not None:
                await _emit_pipeline_event(session_id, q, terminal_event)
            else:
                await _emit_pipeline_event(
                    session_id,
                    q,
                    {
                        "type": "streaming_ended",
                        "message": "分析任务流式输出结束",
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
            await q.put(_GRAPH_STREAM_END)

    token = _pipeline_event_queue.set(q)
    try:
        # 将图执行放入后台，避免阻塞前台事件消费循环。
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
            # SSE 断连时，不取消后台分析任务；让其继续执行并持续落库。
            return
    finally:
        _pipeline_event_queue.reset(token)
