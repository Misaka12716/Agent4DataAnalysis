# sandbox/worker.py
# 在 Cube Sandbox 内执行 Python 脚本。

from __future__ import annotations

from typing import Any, Dict

from sandbox.config import SANDBOX_WORKDIR
from sandbox.files import remote_path
from sandbox.session_manager import ensure_sandbox, get_sandbox
from utils.workspace_manager import is_safe_relative_path


def run_python_in_sandbox(
    session_id: str,
    relative_path: str,
    timeout: int = 300,
) -> Dict[str, Any]:
    if not is_safe_relative_path(relative_path):
        return {
            "relative_path": relative_path,
            "stdout": "",
            "stderr": f"路径不安全: {relative_path}",
            "returncode": -1,
            "success": False,
        }

    try:
        ensure_sandbox(session_id)
        sandbox = get_sandbox(session_id)
        rp = remote_path(relative_path)
        cmd = f"python3 {rp}"
        result = sandbox.commands.run(
            cmd,
            cwd=SANDBOX_WORKDIR,
            timeout=float(timeout),
        )
        stdout = getattr(result, "stdout", None) or ""
        stderr = getattr(result, "stderr", None) or ""
        exit_code = getattr(result, "exit_code", None)
        if exit_code is None:
            exit_code = getattr(result, "returncode", 1)
        return {
            "relative_path": relative_path,
            "stdout": stdout if isinstance(stdout, str) else str(stdout),
            "stderr": stderr if isinstance(stderr, str) else str(stderr),
            "returncode": int(exit_code),
            "success": int(exit_code) == 0,
        }
    except Exception as e:
        err = str(e)
        if "timeout" in err.lower():
            err = "执行超时"
        return {
            "relative_path": relative_path,
            "stdout": "",
            "stderr": err,
            "returncode": -1,
            "success": False,
        }
