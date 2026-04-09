import json
from datetime import datetime
from typing import AsyncGenerator

from db.session_store import SessionStore
from planner.agent_planner import AgentPlanner
from planner.dataframe_reader import read_workspace_excel_schema_and_sample
from coder.workspace_coder import generate_and_write_code
from reporter.report_agent import stream_report
from utils.workspace_manager import list_workspace_files, resolve_workspace_root
from worker.workspace_worker import run_workspace_tasks


def _push_to_session(session_id: str, payload: dict) -> str:
    """更新会话内容并返回 SSE 行。"""
    try:
        fragment = json.dumps(payload, ensure_ascii=False)
        SessionStore.append_content(session_id, fragment + "\n")
    except Exception:
        pass
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def streaming_task_generator(
    session_id: str, input_data: str
) -> AsyncGenerator[str, None]:
    """
    绑定 Session_ID，加载工作区上下文，执行 Planner → Coder → Worker → Reporter。
    每产生一个片段先更新会话「完整内容」+ 版本号，再推送 SSE 给前端。
    """
    try:
        async with AgentPlanner() as planner:
            # 1. Planner（带工作区 Excel 增强）
            plan_data = None
            async for event in planner.run_flow_with_workspace(session_id, input_data):
                yield _push_to_session(session_id, {"type": "planner", "data": event})
                if event.get("type") == "stage_result" and event.get("data"):
                    plan_data = event["data"]

            if not plan_data:
                yield _push_to_session(
                    session_id,
                    {
                        "type": "error",
                        "message": "规划阶段未产出有效结果",
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
                return

            requirement_analysis = (plan_data.get("需求解析") or "").strip()
            steps_outline = (plan_data.get("步骤分解") or "").strip()
            if not requirement_analysis or not steps_outline:
                yield _push_to_session(
                    session_id,
                    {
                        "type": "error",
                        "message": "规划结果缺少需求解析或步骤分解",
                        "data": plan_data,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
                return

            execution_mode = "simple"
            code_file_paths = ["main.py"]
            planner_summary = (plan_data.get("规划全文") or "").strip()
            if not planner_summary:
                planner_summary = json.dumps(
                    {
                        "需求解析": requirement_analysis,
                        "步骤分解": steps_outline,
                    },
                    ensure_ascii=False,
                )

            # 2. Coder：按任务生成代码并写入工作区
            code_specs = [
                {
                    "task_desc": planner_summary or input_data,
                    "requirement_analysis": requirement_analysis,
                    "steps_outline": steps_outline,
                    "relative_path": code_file_paths[0],
                }
            ]
            # 获取工作区文件列表与 Excel 结构，供 Coder 使用真实路径与格式
            workspace_context = {}
            workspace_root = resolve_workspace_root(session_id)
            if workspace_root:
                workspace_context["file_list"] = list_workspace_files(session_id)
                workspace_context["excel_schema"] = read_workspace_excel_schema_and_sample(
                    workspace_root
                )
            coder_results = generate_and_write_code(
                session_id, code_specs, workspace_context=workspace_context
            )
            yield _push_to_session(session_id, {"type": "coder", "data": coder_results})

            # 3. Worker：在工作区内执行代码
            worker_results = run_workspace_tasks(
                session_id, execution_mode, code_file_paths
            )
            yield _push_to_session(session_id, {"type": "worker", "data": worker_results})

            # 4. Reporter：流式报告
            async for chunk in stream_report(
                planner_summary,
                worker_results,
                session_id=session_id,
            ):
                yield _push_to_session(session_id, {"type": "report_chunk", "content": chunk})

            yield _push_to_session(
                session_id,
                {
                    "type": "streaming_ended",
                    "message": "分析任务流式输出结束",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
    except Exception as e:
        yield _push_to_session(
            session_id,
            {
                "type": "streaming_error",
                "error": str(e),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
