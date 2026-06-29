from __future__ import annotations

from runtime.local.commands import LocalCommands
from runtime.local.filesystem import LocalFilesystem
from utils.workspace_manager import init_workspace, resolve_workspace_root


class LocalRuntime:
    backend = "local"

    def __init__(self, session_id: str, workdir: str) -> None:
        self.session_id = session_id
        self.workdir = workdir
        self.files = LocalFilesystem(session_id, workdir)
        self.commands = LocalCommands(session_id, workdir)

    @classmethod
    def bind(cls, session_id: str) -> "LocalRuntime":
        sid = (session_id or "").strip()
        workdir = resolve_workspace_root(sid)
        if not workdir:
            from db.session_store import SessionStore

            row, err = SessionStore.get_session_user(sid)
            if err or not row or not row.get("user_id"):
                raise ValueError(f"无法解析会话工作区: session_id={sid}")
            workdir = init_workspace(int(row["user_id"]), sid)
        return cls(sid, workdir)

    def close(self) -> None:
        return None
