from __future__ import annotations

import logging
from typing import List, Literal, Optional

from runtime.local.runtime import LocalRuntime
from runtime.types import CommandResult, WriteInfo
from sandbox.config import SANDBOX_WORKDIR
from sandbox.files import list_files, remote_path, sync_to_local
from sandbox.session_manager import ensure_sandbox, get_sandbox, pause_sandbox, try_ensure_sandbox
from utils.workspace_manager import is_safe_relative_path, resolve_workspace_root

logger = logging.getLogger(__name__)


class SandboxFilesystem:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def _sandbox(self):
        return get_sandbox(self._session_id)

    def write(self, path: str, data: bytes | str) -> WriteInfo | None:
        rel = (path or "").replace("\\", "/").lstrip("/")
        if not is_safe_relative_path(rel):
            return None
        payload = data.encode("utf-8") if isinstance(data, str) else data
        try:
            self._sandbox().files.write(remote_path(rel), payload)
            sync_to_local(self._session_id)
            return WriteInfo(path=rel, bytes_written=len(payload))
        except Exception:
            logger.debug("sandbox write failed: session=%s path=%s", self._session_id, rel, exc_info=True)
            return None

    def read(self, path: str, format: Literal["bytes", "text"] = "bytes") -> bytes | str | None:
        rel = (path or "").replace("\\", "/").lstrip("/")
        if not is_safe_relative_path(rel):
            return None
        try:
            raw = bytes(self._sandbox().files.read(remote_path(rel), format="bytes"))
        except Exception:
            return None
        if format == "text":
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")
        return raw

    def exists(self, path: str) -> bool:
        rel = (path or "").replace("\\", "/").lstrip("/")
        if not is_safe_relative_path(rel):
            return False
        try:
            return bool(self._sandbox().files.exists(remote_path(rel)))
        except Exception:
            return False

    def list(self, dir_path: str = ".", depth: int = 1) -> List[str]:
        prefix = "" if dir_path in (".", "./", "") else dir_path.strip("/") + "/"
        try:
            all_files = list_files(self._session_id)
        except Exception:
            return []
        if not prefix:
            return all_files
        return sorted(
            rel for rel in all_files if rel.startswith(prefix) and rel != prefix.rstrip("/")
        )


class SandboxCommands:
    def __init__(self, session_id: str, workdir: str | None = None) -> None:
        self._session_id = session_id
        self._workdir = workdir

    def run(self, cmd: str, cwd: str | None = None, timeout: float | None = None) -> CommandResult:
        from runtime.validation import ValidationError, validate_python_command

        try:
            _, rel_script = validate_python_command(
                cmd, self._session_id, workdir=self._workdir
            )
            sandbox = get_sandbox(self._session_id)
            rp = remote_path(rel_script)
            run_cmd = f"python3 {rp}"
            result = sandbox.commands.run(
                run_cmd,
                cwd=cwd or SANDBOX_WORKDIR,
                timeout=float(timeout or 300),
            )
            stdout = getattr(result, "stdout", None) or ""
            stderr = getattr(result, "stderr", None) or ""
            exit_code = getattr(result, "exit_code", None)
            if exit_code is None:
                exit_code = getattr(result, "returncode", 1)
            return CommandResult(
                stdout=stdout if isinstance(stdout, str) else str(stdout),
                stderr=stderr if isinstance(stderr, str) else str(stderr),
                exit_code=int(exit_code),
            )
        except ValidationError as exc:
            return CommandResult(stdout="", stderr=str(exc), exit_code=-1)
        except Exception as exc:
            err = str(exc)
            if "timeout" in err.lower():
                err = "执行超时"
            return CommandResult(stdout="", stderr=err, exit_code=-1)


class SandboxRuntimeAdapter:
    backend = "sandbox"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        ensure_sandbox(session_id)
        root = resolve_workspace_root(session_id)
        self.workdir = root or SANDBOX_WORKDIR
        self.files = SandboxFilesystem(session_id)
        self.commands = SandboxCommands(session_id, workdir=self.workdir)

    @classmethod
    def bind(cls, session_id: str) -> Optional["SandboxRuntimeAdapter"]:
        if not try_ensure_sandbox(session_id):
            return None
        try:
            return cls(session_id)
        except Exception:
            logger.warning("sandbox runtime bind failed: session=%s", session_id, exc_info=True)
            return None

    def close(self) -> None:
        try:
            pause_sandbox(self.session_id)
        except Exception:
            logger.debug("pause sandbox failed: session=%s", self.session_id, exc_info=True)
