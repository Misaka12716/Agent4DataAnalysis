"""编排路由钳制与 Coder 修正后 Worker 重跑的回归测试。"""

from configs.config import MAX_CODER_CORRECTIONS
from orchestrator.routing import (
    SupervisorDecision,
    clamp_route,
    should_correct_code,
    should_skip_regenerate,
)


def _base_state(**overrides):
    state = {
        "plan_data": {"需求解析": "解析", "步骤分解": "步骤"},
        "coder_results": [{"relative_path": "main.py", "success": True}],
        "worker_results": {
            "success": False,
            "error_messages": ["main.py: FileNotFoundError"],
            "results": [{"relative_path": "main.py", "success": False, "stderr": "FileNotFoundError"}],
        },
        "last_completed_stage": "coder",
        "correction_attempts": 1,
        "supervisor_invoke_count": 5,
        "planner_run_count": 1,
        "reporter_done": False,
        "force_reporter": False,
    }
    state.update(overrides)
    return state


def test_clamp_route_forces_worker_after_coder_write():
    """Supervisor 误选 coder 时，代码刚写入/修正后应钳制为 worker。"""
    state = _base_state()
    decision = SupervisorDecision(next_stage="coder", reason="需继续修正", feedback_for_next="")
    route, reason = clamp_route(state, decision)
    assert route == "worker"
    assert "worker" in reason


def test_clamp_route_forces_worker_when_correction_limit_reached():
    """已达 Coder 修正上限且 Worker 仍失败时，禁止再回 coder。"""
    state = _base_state(
        correction_attempts=MAX_CODER_CORRECTIONS,
        last_completed_stage="worker",
    )
    decision = SupervisorDecision(next_stage="coder", reason="继续修正", feedback_for_next="")
    route, reason = clamp_route(state, decision)
    assert route == "worker"
    assert "修正上限" in reason


def test_should_correct_code_when_worker_failed_under_limit():
    state = _base_state(correction_attempts=0)
    assert should_correct_code(state) is True


def test_should_not_correct_code_when_at_limit():
    state = _base_state(correction_attempts=MAX_CODER_CORRECTIONS)
    assert should_correct_code(state) is False


def test_should_skip_regenerate_at_correction_limit():
    state = _base_state(correction_attempts=MAX_CODER_CORRECTIONS)
    assert should_skip_regenerate(state) is True


def test_should_not_skip_regenerate_before_first_worker_run():
    state = _base_state(worker_results=None, correction_attempts=0)
    assert should_skip_regenerate(state) is False
    assert should_correct_code(state) is False


def test_worker_fail_then_coder_correct_routes_to_worker():
    """
    回归：Worker 失败后 Coder 修正完成，Supervisor 应被钳制到 worker 重跑，
    而非基于过期 worker_results 再次进入 coder 循环。
    """
    after_correct = _base_state(
        worker_results=None,
        correction_attempts=1,
        last_completed_stage="coder",
    )
    decision = SupervisorDecision(
        next_stage="coder",
        reason="Worker 因目录不存在失败，需修正",
        feedback_for_next="添加 os.makedirs",
    )
    route, _ = clamp_route(after_correct, decision)
    assert route == "worker"
