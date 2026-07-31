import os

MAX_OUTPUT_CHARS = int(os.getenv("RUNTIME_MAX_OUTPUT_CHARS", str(512 * 1024)))
DEFAULT_COMMAND_TIMEOUT = int(os.getenv("RUNTIME_COMMAND_TIMEOUT", "300"))
RUNNER_PYTHON = os.getenv("RUNNER_PYTHON", "").strip()


def is_sandbox_backend_enabled() -> bool:
    """是否尝试使用 Cube Sandbox 后端（默认关闭，走本地 Runtime）。"""
    return os.getenv("CUBE_SANDBOX_ENABLED", "0") == "1"


def get_runner_python() -> str:
    """
    Worker 代码执行使用的 Python 解释器（独立于 FastAPI/LangGraph 主环境）。

    应指向专用 conda 环境 ``agentPlatform-runner``（已装 ``requirements-runner.txt``）。
    conda ``base`` 为根环境、不可改名，勿当作专用 Runner。
    """
    if RUNNER_PYTHON:
        return RUNNER_PYTHON
    return "python3"
