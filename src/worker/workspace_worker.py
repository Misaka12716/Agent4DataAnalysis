# worker/workspace_worker.py
# 在工作区内调度并执行代码：根据 Planner 的模式（单文件/多文件）执行，cwd 设为工作区根目录。

from typing import List, Dict, Any

from runtime.factory import ensure_runtime
from runtime.validation import build_python_command, is_safe_relative_path


def run_python_in_workspace(
    session_id: str,
    relative_path: str,
    timeout: int = 300,
) -> Dict[str, Any]:
    """
    在工作区根目录下执行单个 Python 文件。
    :return: { "relative_path", "stdout", "stderr", "returncode", "success" }
    """
    if not is_safe_relative_path(relative_path):
        return {
            "relative_path": relative_path,
            "stdout": "",
            "stderr": f"路径不安全: {relative_path}",
            "returncode": -1,
            "success": False,
        }

    rt = ensure_runtime(session_id)
    cmd = build_python_command(relative_path)
    result = rt.commands.run(cmd, cwd=rt.workdir, timeout=float(timeout))
    return {
        "relative_path": relative_path,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.exit_code,
        "success": result.success,
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
    ensure_runtime(session_id)

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
