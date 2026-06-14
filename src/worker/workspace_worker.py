# worker/workspace_worker.py
# 在工作区内调度并执行代码：根据 Planner 的模式（单文件/多文件）执行，cwd 设为工作区根目录。
# 启用 Cube Sandbox 时在隔离 MicroVM 内执行。

import os
import subprocess
from typing import List, Dict, Any

from sandbox.config import is_sandbox_enabled
from utils.workspace_manager import resolve_workspace_root, to_absolute_path


def run_python_in_workspace(
    session_id: str,
    relative_path: str,
    timeout: int = 300,
) -> Dict[str, Any]:
    """
    在工作区根目录下执行单个 Python 文件。
    :return: { "relative_path", "stdout", "stderr", "returncode", "success" }
    """
    if is_sandbox_enabled():
        try:
            from sandbox.worker import run_python_in_sandbox

            return run_python_in_sandbox(session_id, relative_path, timeout=timeout)
        except Exception as e:
            return {
                "relative_path": relative_path,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "success": False,
            }

    root = resolve_workspace_root(session_id)
    if not root:
        return {
            "relative_path": relative_path,
            "stdout": "",
            "stderr": "工作区不存在",
            "returncode": -1,
            "success": False,
        }
    abs_path = to_absolute_path(session_id, relative_path)
    if not abs_path or not os.path.isfile(abs_path):
        return {
            "relative_path": relative_path,
            "stdout": "",
            "stderr": f"文件不存在或路径不安全: {relative_path}",
            "returncode": -1,
            "success": False,
        }
    try:
        result = subprocess.run(
            [os.environ.get("PYTHON", "python3"), abs_path],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "relative_path": relative_path,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "relative_path": relative_path,
            "stdout": "",
            "stderr": "执行超时",
            "returncode": -1,
            "success": False,
        }
    except Exception as e:
        return {
            "relative_path": relative_path,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "success": False,
        }


def run_workspace_tasks(
    session_id: str,
    execution_mode: str,
    code_file_paths: List[str],
    timeout_per_file: int = 300,
) -> Dict[str, Any]:
    """
    根据执行模式运行工作区内的代码文件。
    :param session_id: 会话 ID
    :param execution_mode: "simple" 单文件 | "complex" 多文件
    :param code_file_paths: 相对路径列表（如 ["main.py"] 或 ["task_1.py", "task_2.py"]）
    :param timeout_per_file: 每个文件执行超时（秒）
    :return: { "success", "results": [ run_python_in_workspace 的返回值, ... ], "logs", "error_messages" }
    """
    if is_sandbox_enabled():
        try:
            from sandbox.session_manager import ensure_sandbox

            ensure_sandbox(session_id)
        except Exception as e:
            return {
                "success": False,
                "results": [],
                "logs": "",
                "error_messages": [str(e)],
            }

    results = []
    logs = []
    errors = []
    for rel_path in code_file_paths:
        one = run_python_in_workspace(session_id, rel_path, timeout=timeout_per_file)
        results.append(one)
        if one.get("stdout"):
            logs.append(f"[{rel_path} stdout]\n{one['stdout']}")
        if one.get("stderr"):
            logs.append(f"[{rel_path} stderr]\n{one['stderr']}")
        if not one.get("success"):
            errors.append(f"{rel_path}: {one.get('stderr', '')}")
    overall = all(r.get("success") for r in results)
    return {
        "success": overall,
        "results": results,
        "logs": "\n\n".join(logs),
        "error_messages": errors,
    }
