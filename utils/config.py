import os
from typing import List

# --------------------------
# 通用路径配置
# --------------------------
PATH = "/data/agent_platform"  # 文件夹的固定位置

# --------------------------
# 模型相关配置
# --------------------------
SUPPORTED_MODELS: List[str] = [
    "qwen3:30b",
    "qwen3:8b",
    "qwen3:4b",
]  # 支持的模型列表
DEFAULT_MODEL = "qwen3:8b"  # 默认使用的模型

# --------------------------
# API 相关配置
# --------------------------
OPENAI_COMPATIBLE_API_BASE = "http://localhost:11434/v1"  # 兼容OpenAI的API基础地址
API_KEY = ""  # API访问密钥（为空时可能表示无需密钥）

# --------------------------
# 数据库连接配置（核心参数）
# --------------------------
# 数据库主机地址（优先从环境变量读取，默认localhost）
MYSQL_HOST = "localhost"
# 数据库用户名（默认root）
MYSQL_USER = "root"
# 数据库密码
MYSQL_PASSWORD = "pku123"
# 数据库名称
MYSQL_DB = "agent_platform"
# 数据库端口（默认3306）
MYSQL_PORT = int(3306)
# 字符编码（支持emoji）
MYSQL_CHARSET = "utf8mb4"
