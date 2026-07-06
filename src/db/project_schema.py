# db/project_schema.py
# 项目与项目资产表结构定义（Schema / TypedDict / DDL）

from typing import Optional, TypedDict

TABLE_PROJECTS = "projects"
TABLE_PROJECT_ASSETS = "project_assets"

PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_ARCHIVED = "archived"
VALID_PROJECT_STATUSES = (PROJECT_STATUS_ACTIVE, PROJECT_STATUS_ARCHIVED)

ASSET_TYPE_UPLOAD = "upload"
ASSET_TYPE_ANALYSIS_OUTPUT = "analysis_output"
VALID_ASSET_TYPES = (ASSET_TYPE_UPLOAD, ASSET_TYPE_ANALYSIS_OUTPUT)

PROJECT_SUBDIRS = ("raw", "processed", "outputs", "archive", "sessions")

# 每用户唯一的内置默认项目（DB 存 internal name，API 展示 display name）
DEFAULT_PROJECT_INTERNAL_NAME = "__personal_default__"
DEFAULT_PROJECT_DISPLAY_NAME = "个人默认"
RESERVED_PROJECT_NAMES = frozenset(
    {DEFAULT_PROJECT_INTERNAL_NAME, DEFAULT_PROJECT_DISPLAY_NAME}
)

PROJECT_COLUMNS = [
    "id",
    "user_id",
    "name",
    "status",
    "workspace_abs_path",
    "created_at",
    "updated_at",
]

PROJECT_ASSET_COLUMNS = [
    "id",
    "project_id",
    "session_id",
    "asset_type",
    "relative_path",
    "original_filename",
    "file_category",
    "created_at",
]


class ProjectRow(TypedDict, total=False):
    id: int
    user_id: int
    name: str
    status: str
    workspace_abs_path: str
    created_at: Optional[str]
    updated_at: Optional[str]


class ProjectAssetRow(TypedDict, total=False):
    id: int
    project_id: int
    session_id: Optional[str]
    asset_type: str
    relative_path: str
    original_filename: Optional[str]
    file_category: Optional[str]
    created_at: Optional[str]


PROJECTS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PROJECTS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '所属用户ID',
    name VARCHAR(255) NOT NULL COMMENT '项目名称',
    status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active|archived',
    workspace_abs_path VARCHAR(512) NOT NULL COMMENT '项目工作区绝对路径',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '项目表';
"""

PROJECT_ASSETS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PROJECT_ASSETS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT NOT NULL COMMENT '所属项目ID',
    session_id VARCHAR(64) NULL COMMENT '关联会话ID',
    asset_type VARCHAR(32) NOT NULL COMMENT 'upload|analysis_output',
    relative_path VARCHAR(512) NOT NULL COMMENT '相对项目根目录的路径',
    original_filename VARCHAR(255) NULL COMMENT '原始文件名',
    file_category VARCHAR(32) NULL COMMENT 'table|image|text|other',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_project_id (project_id),
    INDEX idx_session_id (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '项目资产登记表';
"""
