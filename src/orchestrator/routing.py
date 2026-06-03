"""编排路由钳制与 Coder/Worker 阶段切换的纯函数（无 LLM/DB 依赖，便于单测）。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from configs.config import MAX_CODER_CORRECTIONS, MAX_PLANNER_RETRIES, MAX_SUPERVISOR_INVOCATIONS


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


PipelineState = Dict[str, Any]


def plan_is_valid(plan_data: Optional[Dict[str, Any]]) -> bool:
    if not plan_data:
        return False
    ra = (plan_data.get("需求解析") or "").strip()
    so = (plan_data.get("步骤分解") or "").strip()
    return bool(ra and so)


def code_write_succeeded(coder_results: Optional[List[Dict[str, Any]]]) -> bool:
    if not coder_results:
        return False
    return any(r.get("success") for r in coder_results)


def should_correct_code(state: PipelineState) -> bool:
    """Worker 失败且未达修正上限时，进入 Coder 修复模式。"""
    wr = state.get("worker_results")
    return (
        wr is not None
        and not wr.get("success", False)
        and code_write_succeeded(state.get("coder_results"))
        and int(state.get("correction_attempts") or 0) < MAX_CODER_CORRECTIONS
    )


def should_skip_regenerate(state: PipelineState) -> bool:
    """已达修正上限且 Worker 仍失败时，禁止 generate 覆盖已有代码。"""
    wr = state.get("worker_results")
    return (
        wr is not None
        and not wr.get("success", False)
        and code_write_succeeded(state.get("coder_results"))
        and int(state.get("correction_attempts") or 0) >= MAX_CODER_CORRECTIONS
    )


def clamp_route(state: PipelineState, decision: SupervisorDecision) -> tuple[str, str]:
    """
    对 Supervisor 的路由决策做安全钳制，返回 (next_route, reason)。
    """
    raw = decision.next_stage
    reason = (decision.reason or "").strip()
    extra = ""

    plan_ok = plan_is_valid(state.get("plan_data"))
    code_ok = code_write_succeeded(state.get("coder_results"))
    wr = state.get("worker_results")
    rep_done = bool(state.get("reporter_done"))
    force_rep = bool(state.get("force_reporter"))
    sup_ct = int(state.get("supervisor_invoke_count") or 0)
    last = (state.get("last_completed_stage") or "").strip()
    corr = int(state.get("correction_attempts") or 0)

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

    if plan_ok and code_ok and last == "coder" and raw == "coder":
        raw = "worker"
        extra = "（代码已写入/修正，改为 worker 执行）"

    if (
        plan_ok
        and code_ok
        and corr >= MAX_CODER_CORRECTIONS
        and wr is not None
        and not wr.get("success", False)
        and raw == "coder"
    ):
        raw = "worker"
        extra = "（已达 Coder 修正上限，改为 worker 重跑）"

    if extra:
        reason = f"{reason} {extra}".strip()

    return raw, reason


def supervisor_allowed_hint(state: PipelineState) -> str:
    last = (state.get("last_completed_stage") or "").strip()
    plan_ok = plan_is_valid(state.get("plan_data"))
    code_ok = code_write_succeeded(state.get("coder_results"))
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
    if last == "coder" and code_ok:
        lines.append("代码已写入/修正：应选择 worker 重新执行（勿再次进入 coder）。")
    elif wfail:
        corr = int(state.get("correction_attempts") or 0)
        if corr >= MAX_CODER_CORRECTIONS:
            lines.append(
                "Worker 未成功且已达 Coder 修正上限：应选择 worker 重跑或 reporter 收敛，勿再 generate 覆盖代码。"
            )
        else:
            lines.append(
                "Worker 未成功：通常应选择 coder，并在 feedback_for_next 中粘贴 stderr/关键错误。"
            )
    if not plan_ok:
        lines.append("当前无有效规划：应选择 planner。")
    elif last == "planner" and plan_ok:
        lines.append("规划刚完成：通常应选择 coder。")
    elif last == "worker" and wr and wr.get("success"):
        lines.append("执行成功：通常应选择 reporter。")
    elif last == "reporter" and rep_done:
        lines.append("报告已流式完成：必须选择 finish。")
    return "\n".join(lines)
