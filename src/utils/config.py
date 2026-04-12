import os
from typing import List

# --------------------------
# 通用路径配置
# --------------------------
PATH = "/data/agent_platform/src"  # 文件夹的固定位置
TEMP_FOLDER = "/data/agent_platform/tmp/"  # 临时文件存储路径
UPLOAD_FOLDER = os.path.join(TEMP_FOLDER, "uploads/")  # 上传文件存储路径
DOWNLOAD_FOLDER = os.path.join(TEMP_FOLDER, "downloads/")  # 下载文件存储路径

# --------------------------
# 模型相关配置
# --------------------------
SUPPORTED_MODELS: List[str] = [
    "qwen3:32b",
    "qwen3:8b",
    "qwen3:4b",
    "qwen2.5:7b",
    "qwen2.5:3b",
    "qweb3-coder:30b",
]  # 支持的模型列表
DEFAULT_MODEL = "qwen3:8b"  # 默认使用的模型（勿尾随空格，避免兼容 API 拒收）
DEFAULT_CODER_MODEL = "qwen3-coder:30b"  # 默认使用的模型
# 顶层编排 Supervisor 路由模型（可与主模型相同）
DEFAULT_ORCHESTRATOR_MODEL = os.getenv("DEFAULT_ORCHESTRATOR_MODEL", DEFAULT_MODEL)
# 编排：Supervisor 调用次数上限、子阶段重试上限
MAX_SUPERVISOR_INVOCATIONS = int(os.getenv("MAX_SUPERVISOR_INVOCATIONS", "24"))
MAX_CODER_CORRECTIONS = int(os.getenv("MAX_CODER_CORRECTIONS", "5"))
MAX_PLANNER_RETRIES = int(os.getenv("MAX_PLANNER_RETRIES", "4"))
LANGUAGE = "zh"  # 使用的语言(zn/en)


# 是否开启大模型全流程日志记录
ENABLE_MODEL_LOG: bool = True

# --------------------------
# API 相关配置
# --------------------------
OPENAI_COMPATIBLE_API_BASE = "http://localhost:11434/v1"  # 兼容OpenAI的API基础地址
API_KEY = ""  # API访问密钥（为空时可能表示无需密钥）
WORKFLOW_API_BASE = "162.105.89.4/workflow/api/"  # 工作流API基础地址

# --------------------------
# MYSQL连接配置（核心参数）
# --------------------------
# MYSQL主机地址（优先从环境变量读取，默认localhost）
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
# 数据库用户名（默认root）
MYSQL_USER = "root"
# 数据库密码
MYSQL_PASSWORD = "88888888"
# 数据库名称
MYSQL_DB = "agent_platform"
# 数据库端口（默认3306）
# MYSQL端口（优先从环境变量读取，默认3306）
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
# 字符编码（支持emoji）
MYSQL_CHARSET = "utf8mb4"
