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
    "qwen3:30b",
    "qwen3:8b",
    "qwen3:4b",
]  # 支持的模型列表
DEFAULT_MODEL = "qwen3:8b"  # 默认使用的模型
LANGUAGE = "zh"  # 使用的语言(zn/en)

# --------------------------
# API 相关配置
# --------------------------
OPENAI_COMPATIBLE_API_BASE = "http://localhost:11434/v1"  # 兼容OpenAI的API基础地址
API_KEY = ""  # API访问密钥（为空时可能表示无需密钥）

# --------------------------
# MYSQL连接配置（核心参数）
# --------------------------
# MYSQL主机地址（优先从环境变量读取，默认localhost）
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
# 数据库用户名（默认root）
MYSQL_USER = "root"
# 数据库密码
MYSQL_PASSWORD = "pku123"
# 数据库名称
MYSQL_DB = "agent_platform"
# 数据库端口（默认3306）
# MYSQL端口（优先从环境变量读取，默认3306）
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
# 字符编码（支持emoji）
MYSQL_CHARSET = "utf8mb4"

# --------------------------
# neo4j连接配置（核心参数）
# --------------------------
# neo4j主机地址（优先从环境变量读取，默认localhost）
NEO4J_HOST = os.getenv("NEO4J_HOST", "localhost")
# neo4j用户名（默认neo4j）
NEO4J_USER = "neo4j"
# neo4j密码
NEO4J_PASSWORD = "pku12345"
# neo4j数据库名称（默认neo4j）
NEO4J_DB = "neo4j"
# neo4j端口（默认7687）
NEO4J_PORT = int(os.getenv("NEO4J_PORT", 7687))

# --------------------------
# 工具池核心配置
# --------------------------
# 存储工具元信息的表名
TABLE_TOOLS_META_INFO = "tools"
# 存储工具标签的表名
TABLE_TOOLS_TAGS = "tool_tags"
