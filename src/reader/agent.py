from typing import Any, Dict, Optional

from utils.model_logger import log_phase_end, log_phase_start

from reader.formatters import workspace_digest_to_markdown
from reader.graph import get_reader_graph


class WorkspaceReaderAgent:
    """工作区 Reader LangGraph 智能体封装。"""

    def __init__(self) -> None:
        self._graph = get_reader_graph()

    def invoke(
        self,
        workspace_abs: str,
        *,
        session_id: str = "",
        lang: str = "zh",
    ) -> Dict[str, Any]:
        initial = {
            "workspace_root": workspace_abs,
            "session_id": session_id,
            "lang": lang,
            "file_inventory": [],
            "file_digests": {},
            "errors": [],
        }
        final = self._graph.invoke(initial)
        return final.get("workspace_digest") or {
            "files": {},
            "summary": "Reader 未返回结果",
        }

    def invoke_with_markdown(
        self,
        workspace_abs: str,
        *,
        session_id: str = "",
        lang: str = "zh",
    ) -> tuple[Dict[str, Any], str]:
        initial = {
            "workspace_root": workspace_abs,
            "session_id": session_id,
            "lang": lang,
            "file_inventory": [],
            "file_digests": {},
            "errors": [],
        }
        final = self._graph.invoke(initial)
        digest = final.get("workspace_digest") or {"files": {}, "summary": ""}
        md = (final.get("markdown_summary") or "").strip()
        if not md:
            md = workspace_digest_to_markdown(digest).strip() or digest.get("summary", "")
        return digest, md


_reader_singleton: Optional[WorkspaceReaderAgent] = None


def _get_reader() -> WorkspaceReaderAgent:
    global _reader_singleton
    if _reader_singleton is None:
        _reader_singleton = WorkspaceReaderAgent()
    return _reader_singleton


def run_workspace_reader_sync(
    workspace_abs: str,
    *,
    session_id: str = "",
    lang: str = "zh",
) -> Dict[str, Any]:
    sid = session_id or "reader"
    log_phase_start(sid, "reader", {"workspace": workspace_abs, "lang": lang})
    digest = _get_reader().invoke(workspace_abs, session_id=session_id, lang=lang)
    log_phase_end(
        sid,
        "reader",
        {
            "file_count": len(digest.get("files") or {}),
            "summary": (digest.get("summary") or "")[:200],
        },
    )
    return digest


async def run_workspace_reader(
    workspace_abs: str,
    *,
    session_id: str = "",
    lang: str = "zh",
) -> Dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(
        run_workspace_reader_sync,
        workspace_abs,
        session_id=session_id,
        lang=lang,
    )


def run_workspace_reader_with_markdown_sync(
    workspace_abs: str,
    *,
    session_id: str = "",
    lang: str = "zh",
) -> tuple[Dict[str, Any], str]:
    return _get_reader().invoke_with_markdown(
        workspace_abs, session_id=session_id, lang=lang
    )


__all__ = [
    "WorkspaceReaderAgent",
    "run_workspace_reader",
    "run_workspace_reader_sync",
    "run_workspace_reader_with_markdown_sync",
    "workspace_digest_to_markdown",
    "excel_schema_from_digest",
]
