import json
import asyncio
from datetime import datetime
from typing import AsyncGenerator, Dict, Any, Tuple

from db.session_store import SessionStore
from configs.config import LANGUAGE


def _format_sse(payload: dict) -> str:
    """将事件格式化为 SSE 行。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _parse_json_events(content: str) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    for raw in (content or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _is_terminal_event(event: Dict[str, Any]) -> bool:
    return str(event.get("type") or "") in {"streaming_ended", "streaming_error", "error"}


def _extract_increment_event(prev_content: str, curr_content: str) -> Dict[str, Any] | None:
    """
    从累计内容中提取“本次版本新增/更新”的最后一条事件。
    当 llm_chunk 被合并时，行数可能不变，因此不能仅依赖追加行数。
    """
    prev_events = _parse_json_events(prev_content)
    curr_events = _parse_json_events(curr_content)
    if not curr_events:
        return None
    if len(curr_events) > len(prev_events):
        return curr_events[-1]
    if len(curr_events) == len(prev_events):
        for idx in range(len(curr_events) - 1, -1, -1):
            if curr_events[idx] != prev_events[idx]:
                return curr_events[idx]
    return None


def _latest_event_from_content(content: str) -> Dict[str, Any] | None:
    events = _parse_json_events(content)
    if not events:
        return None
    return events[-1]


def _fetch_latest_snapshot(session_id: str) -> Tuple[str, int]:
    try:
        content, version = SessionStore.get_latest_content(session_id)
    except Exception:
        return "", 0
    return content or "", int(version or 0)


async def streaming_task_generator(
    session_id: str, input_data: str
) -> AsyncGenerator[str, None]:
    """
    绑定 Session_ID，经顶层编排（Supervisor + LangGraph）驱动 Planner / Coder / Worker / Reporter，
    支持失败回溯；每产生一个片段先更新会话「完整内容」+ 版本号，再推送 SSE 给前端。
    """
    try:
        from orchestrator.analysis_pipeline_graph import run_orchestrated_analysis_stream

        async for payload in run_orchestrated_analysis_stream(
            session_id, input_data, lang=LANGUAGE
        ):
            yield _format_sse(payload)
    except Exception as e:
        try:
            SessionStore.append_content(
                session_id,
                json.dumps(
                    {
                        "type": "streaming_error",
                        "error": str(e),
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
        except Exception:
            pass
        yield _format_sse(
            {
                "type": "streaming_error",
                "error": str(e),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )


async def reconnect_streaming_task_generator(
    session_id: str,
    poll_interval_seconds: float = 0.5,
) -> AsyncGenerator[str, None]:
    """
    断线重连流：
    1) 先返回当前库中锁存快照（完整内容 + 版本号）；
    2) 若任务未结束，持续轮询 session_content 并转发新增事件，直到终态事件。
    """
    content, version = _fetch_latest_snapshot(session_id)
    yield _format_sse(
        {
            "type": "snapshot",
            "session_id": session_id,
            "content": content,
            "version": version,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )

    last_event = _latest_event_from_content(content)
    if last_event and _is_terminal_event(last_event):
        return

    last_content = content
    while True:
        await asyncio.sleep(poll_interval_seconds)
        current_content, current_version = _fetch_latest_snapshot(session_id)
        if current_version <= version:
            continue
        event = _extract_increment_event(last_content, current_content)
        version = current_version
        last_content = current_content
        if not event:
            continue
        yield _format_sse(event)
        if _is_terminal_event(event):
            return
