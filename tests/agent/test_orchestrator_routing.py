"""编排路由钳制与 Coder 修正后 Worker 重跑的回归测试。"""

from configs.config import MAX_CODER_CORRECTIONS
from orchestrator.routing import (
    SupervisorDecision,
    clamp_route,
    has_analyzable_workspace,
    should_correct_code,
    should_skip_regenerate,
)


def _analyzable_workspace_context():
    return {
        "file_list": ["data.csv"],
        "workspace_digest": {
            "files": {
                "data.csv": {
                    "file_type": "table",
                    "relative_path": "data.csv",
                }
            }
        },
    }


def _empty_workspace_context():
    """仅有 SESSION_MEMORY / main.py，视为无可分析数据。"""
    return {
        "file_list": ["SESSION_MEMORY.md", "main.py"],
        "workspace_digest": {
            "files": {
                "main.py": {
                    "file_type": "binary",
                    "relative_path": "main.py",
                }
            }
        },
    }


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
        # 有数据场景默认值，避免 no_data 钳制干扰既有回归用例
        "workspace_context": _analyzable_workspace_context(),
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


def test_has_analyzable_workspace_false_for_memory_and_main_only():
    assert has_analyzable_workspace(_empty_workspace_context()) is False
    assert has_analyzable_workspace({}) is False
    assert has_analyzable_workspace(None) is False


def test_has_analyzable_workspace_true_for_table():
    assert has_analyzable_workspace(_analyzable_workspace_context()) is True


def test_has_analyzable_workspace_true_for_text_json():
    ctx = {
        "file_list": ["data.json"],
        "workspace_digest": {
            "files": {
                "data.json": {
                    "file_type": "text",
                    "relative_path": "data.json",
                }
            }
        },
    }
    assert has_analyzable_workspace(ctx) is True


def test_has_analyzable_workspace_true_for_text_by_extension():
    ctx = {
        "file_list": ["notes.txt"],
        "workspace_digest": {"files": {}},
    }
    assert has_analyzable_workspace(ctx) is True


def test_clamp_route_no_data_forces_reporter_instead_of_coder():
    """规划已通过但工作区无数据时，禁止硬走 coder/worker。"""
    state = _base_state(
        workspace_context=_empty_workspace_context(),
        coder_results=[],
        worker_results=None,
        last_completed_stage="planner",
        correction_attempts=0,
    )
    decision = SupervisorDecision(
        next_stage="coder",
        reason="开始写综述脚本",
        feedback_for_next="",
    )
    route, reason = clamp_route(state, decision)
    assert route == "reporter"
    assert "无可分析数据" in reason


def test_clamp_route_no_data_forces_reporter_instead_of_worker():
    state = _base_state(
        workspace_context=_empty_workspace_context(),
        coder_results=[{"relative_path": "main.py", "success": True}],
        worker_results=None,
        last_completed_stage="coder",
        correction_attempts=0,
    )
    decision = SupervisorDecision(next_stage="worker", reason="执行", feedback_for_next="")
    route, reason = clamp_route(state, decision)
    assert route == "reporter"
    assert "无可分析数据" in reason
