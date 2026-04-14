import json
from datetime import datetime
from typing import AsyncGenerator

from db.session_store import SessionStore
from utils.config import LANGUAGE


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
    绑定 Session_ID，经顶层编排（Supervisor + LangGraph）驱动 Planner / Coder / Worker / Reporter，
    支持失败回溯；每产生一个片段先更新会话「完整内容」+ 版本号，再推送 SSE 给前端。
    """
    try:
        from orchestrator.analysis_pipeline_graph import run_orchestrated_analysis_stream

        ended_ok = True
        async for payload in run_orchestrated_analysis_stream(
            session_id, input_data, lang=LANGUAGE
        ):
            t = payload.get("type")
            if t == "streaming_error":
                ended_ok = False
            if t == "error":
                ended_ok = False
            yield _push_to_session(session_id, payload)

        if ended_ok:
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
