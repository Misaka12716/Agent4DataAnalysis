import os
from pathlib import Path
from typing import List

# 幂等加载仓库根 .env（override=False：已有进程环境变量优先）
try:
    from dotenv import load_dotenv

    _root_env = Path(__file__).resolve().parents[2] / ".env"
    if _root_env.is_file():
        load_dotenv(_root_env, override=False)
except ImportError:
    pass

# --------------------------
# 通用路径配置
# --------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
PATH = str(_REPO_ROOT / "src")
TEMP_FOLDER = os.getenv("TEMP_FOLDER", str(_REPO_ROOT / "tmp"))
if not TEMP_FOLDER.endswith(os.sep):
    TEMP_FOLDER = TEMP_FOLDER + os.sep
UPLOAD_FOLDER = os.path.join(TEMP_FOLDER, "uploads/")
DOWNLOAD_FOLDER = os.path.join(TEMP_FOLDER, "downloads/")

# --------------------------
# 模型相关配置（部署与分工见 docs/Models.md）
# --------------------------
GENERAL_MODEL = "glm-4.7-flash:q4_K_M"  # 通用文本默认
CODER_MODEL = "qwen3-coder:30b"  # Coder 专用
VISION_MODEL = "deepseek-ocr:latest"  # OCR / 图片文字识别

SUPPORTED_MODELS: List[str] = [
    GENERAL_MODEL ,
    CODER_MODEL,
    VISION_MODEL,
    "qwen2.5:14b",
    "qwen2.5:7b",
]
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", GENERAL_MODEL)   # glm-4.7-flash:q4_K_M
DEFAULT_CODER_MODEL = os.getenv("DEFAULT_CODER_MODEL", CODER_MODEL)
# 顶层编排 Supervisor 路由模型（可与主模型相同）
DEFAULT_ORCHESTRATOR_MODEL = os.getenv("DEFAULT_ORCHESTRATOR_MODEL", DEFAULT_MODEL)
# Reader 智能体：文件摘要与可选 Vision
DEFAULT_READER_MODEL = os.getenv("DEFAULT_READER_MODEL", DEFAULT_MODEL)
DEFAULT_VISION_MODEL = os.getenv("DEFAULT_VISION_MODEL", VISION_MODEL)
READER_TABLE_SAMPLE_ROWS = int(os.getenv("READER_TABLE_SAMPLE_ROWS", "5"))
READER_TEXT_PREVIEW_CHARS = int(os.getenv("READER_TEXT_PREVIEW_CHARS", "4000"))
READER_ENABLE_LLM_TABLE_HEADER = os.getenv("READER_ENABLE_LLM_TABLE_HEADER", "0") == "1"
# 编排：Supervisor 调用次数上限、子阶段重试上限
MAX_SUPERVISOR_INVOCATIONS = int(os.getenv("MAX_SUPERVISOR_INVOCATIONS", "24"))
MAX_CODER_CORRECTIONS = int(os.getenv("MAX_CODER_CORRECTIONS", "5"))
MAX_PLANNER_RETRIES = int(os.getenv("MAX_PLANNER_RETRIES", "4"))
# 单次 LLM HTTP 请求超时（秒）；防止 Coder/Supervisor 同步调用永久挂起
LLM_REQUEST_TIMEOUT = float(os.getenv("LLM_REQUEST_TIMEOUT", "180"))
LANGUAGE = "zh"  # 使用的语言(zn/en)

# 会话记忆 SESSION_MEMORY.md（工作区内 Markdown，供各智能体提示词引用）
SESSION_MEMORY_ENABLED = os.getenv("SESSION_MEMORY_ENABLED", "1") == "1"
SESSION_MEMORY_PROMPT_MAX_CHARS = int(os.getenv("SESSION_MEMORY_PROMPT_MAX_CHARS", "6000"))

# --------------------------
# 执行 Runtime（默认本地；可选 Cube Sandbox 后端）
# --------------------------
from runtime.config import (  # noqa: E402
    DEFAULT_COMMAND_TIMEOUT,
    MAX_OUTPUT_CHARS,
    RUNNER_PYTHON,
    get_runner_python,
    is_sandbox_backend_enabled,
)

# 兼容旧引用
is_sandbox_enabled = is_sandbox_backend_enabled
CUBE_SANDBOX_ENABLED = is_sandbox_backend_enabled()
WORKSPACE_ROOT = "."


# 是否开启大模型全流程日志记录
ENABLE_MODEL_LOG: bool = True

# --------------------------
# API 相关配置（主键：OPENAI_API_*；旧名 API_KEY / OPENAI_COMPATIBLE_API_BASE 为兼容别名）
# --------------------------
API_KEY = os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY") or ""
OPENAI_COMPATIBLE_API_BASE = (
    os.getenv("OPENAI_COMPATIBLE_API_BASE")
    or os.getenv("OPENAI_API_BASE")
    or "http://localhost:11434/v1"
)
WORKFLOW_API_BASE = os.getenv("WORKFLOW_API_BASE", "162.105.89.4/workflow/api/")

# --------------------------
# MYSQL连接配置（权威来源：仓库根 .env 的 MYSQL_*）
# --------------------------
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DB = os.getenv("MYSQL_DB", "agent_platform")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3308"))
MYSQL_CHARSET = os.getenv("MYSQL_CHARSET", "utf8mb4")
