# db/models.py
# 核心表结构定义（仅 Schema/模型，表由运维或迁移脚本后续创建）
# 基于 utils/mysql_utils.py 的 MySQLHandler 能力设计

from typing import TypedDict, Optional, Any
from datetime import datetime

# -------------------------- 表名常量 --------------------------
TABLE_USERS = "users"
TABLE_SESSION_USER = "session_user"
TABLE_SESSION_CONTENT = "session_content"

# -------------------------- 用户表 --------------------------
# 存储用户名、手机、邮箱、密码哈希等基础信息，便于扩展
USER_COLUMNS = [
    "id",           # BIGINT AUTO_INCREMENT PRIMARY KEY
    "username",     # VARCHAR(128) NOT NULL UNIQUE COMMENT '用户名'
    "phone",        # VARCHAR(32) DEFAULT NULL COMMENT '手机号'
    "email",        # VARCHAR(256) DEFAULT NULL COMMENT '邮箱'
    "password_hash",# VARCHAR(256) NOT NULL COMMENT '密码哈希'
    "created_at",   # TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    "updated_at",   # TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    # 可扩展: avatar_url, role, status 等
]


class UserRow(TypedDict, total=False):
    id: int
    username: str
    phone: Optional[str]
    email: Optional[str]
    password_hash: str
    created_at: Optional[str]
    updated_at: Optional[str]


# 建表参考（不在此模块执行，仅文档）
USER_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_USERS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(128) NOT NULL UNIQUE COMMENT '用户名',
    phone VARCHAR(32) DEFAULT NULL COMMENT '手机号',
    email VARCHAR(256) DEFAULT NULL COMMENT '邮箱',
    password_hash VARCHAR(256) NOT NULL COMMENT '密码哈希',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '用户表';
"""

# -------------------------- 会话-用户关联表 --------------------------
# Session_ID 与 User_ID 映射；支持双向高效查询；存储该会话对应的工作区绝对路径
SESSION_USER_COLUMNS = [
    "id",
    "session_id",   # VARCHAR(64) NOT NULL UNIQUE
    "user_id",      # BIGINT NOT NULL
    "workspace_abs_path",  # VARCHAR(512) NOT NULL COMMENT '工作区绝对路径'
    "created_at",
    "updated_at",
]

# 建议索引: INDEX idx_user_id (user_id), UNIQUE KEY uk_session_id (session_id)


class SessionUserRow(TypedDict, total=False):
    id: int
    session_id: str
    user_id: int
    workspace_abs_path: str
    created_at: Optional[str]
    updated_at: Optional[str]


SESSION_USER_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SESSION_USER} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL UNIQUE COMMENT '会话ID',
    user_id BIGINT NOT NULL COMMENT '用户ID',
    workspace_abs_path VARCHAR(512) NOT NULL COMMENT '工作区绝对路径',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '会话-用户关联表';
"""

# -------------------------- 会话内容存储表 --------------------------
# 每个会话的「完整累计内容」+「版本号/片段序号」——断线重连核心
SESSION_CONTENT_COLUMNS = [
    "id",
    "session_id",   # VARCHAR(64) NOT NULL
    "version",      # INT NOT NULL DEFAULT 0 COMMENT '版本号/片段序号，单调递增'
    "content",      # LONGTEXT NOT NULL COMMENT '完整累计内容（或增量片段，由业务约定）'
    "created_at",
]

# 建议: UNIQUE KEY uk_session_version (session_id, version) 或 PRIMARY KEY (session_id, version)


class SessionContentRow(TypedDict, total=False):
    id: int
    session_id: str
    version: int
    content: str
    created_at: Optional[str]


SESSION_CONTENT_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SESSION_CONTENT} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL COMMENT '会话ID',
    version INT NOT NULL DEFAULT 0 COMMENT '版本号/片段序号',
    content LONGTEXT NOT NULL COMMENT '完整累计内容',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_session_version (session_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '会话内容存储表';
"""
