# db/resource_schema.py
# 个人资源管理表结构：文件空间 / 数据集 / 模型库

from __future__ import annotations

from typing import Any, List, Optional, TypedDict

TABLE_USER_FILES = "user_files"
TABLE_USER_DATASETS = "user_datasets"
TABLE_USER_DATASET_VERSIONS = "user_dataset_versions"
TABLE_USER_MODELS = "user_models"

VALID_NODE_TYPES = ("folder", "file")
VALID_FILE_CATEGORIES = (
    "table",
    "image",
    "document",
    "imaging",
    "text",
    "binary",
    "other",
)
VALID_DATASET_STATUS = ("active", "archived")
VALID_MODEL_STATUS = ("active", "deleted")
VALID_MODEL_SOURCES = ("manual", "clinical_risk")


class UserFileRow(TypedDict, total=False):
    id: int
    user_id: int
    parent_id: Optional[int]
    name: str
    node_type: str
    category: str
    mime: Optional[str]
    size_bytes: int
    storage_path: Optional[str]
    checksum: Optional[str]
    tags: Any
    created_at: Optional[str]
    updated_at: Optional[str]
    deleted_at: Optional[str]


class UserDatasetRow(TypedDict, total=False):
    id: int
    user_id: int
    name: str
    description: Optional[str]
    category: str
    source_file_id: Optional[int]
    current_version: int
    status: str
    created_at: Optional[str]
    updated_at: Optional[str]


class UserDatasetVersionRow(TypedDict, total=False):
    id: int
    dataset_id: int
    version: int
    storage_path: str
    row_count: int
    column_count: int
    schema_json: Any
    missing_stats_json: Any
    preview_json: Any
    note: Optional[str]
    created_at: Optional[str]


class UserModelRow(TypedDict, total=False):
    id: int
    user_id: int
    model_name: str
    framework: str
    model_type: Optional[str]
    task_type: Optional[str]
    features: Any
    metrics: Any
    params: Any
    file_path: str
    source: str
    source_ref_id: Optional[int]
    status: str
    created_at: Optional[str]
    updated_at: Optional[str]


DDL_USER_FILES = f"""
CREATE TABLE IF NOT EXISTS {TABLE_USER_FILES} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '所属用户',
    parent_id BIGINT DEFAULT NULL COMMENT '父文件夹 id，根目录为 NULL',
    name VARCHAR(512) NOT NULL COMMENT '节点名称',
    node_type VARCHAR(16) NOT NULL COMMENT 'folder / file',
    category VARCHAR(32) NOT NULL DEFAULT 'other' COMMENT '智能分类类别',
    mime VARCHAR(128) DEFAULT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    storage_path VARCHAR(1024) DEFAULT NULL COMMENT '磁盘绝对路径（仅 file）',
    checksum VARCHAR(64) DEFAULT NULL,
    tags JSON DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL DEFAULT NULL,
    INDEX idx_user_parent (user_id, parent_id),
    INDEX idx_user_deleted (user_id, deleted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个人文件空间节点';
"""

DDL_USER_DATASETS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_USER_DATASETS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    name VARCHAR(256) NOT NULL,
    description TEXT,
    category VARCHAR(32) NOT NULL DEFAULT 'table',
    source_file_id BIGINT DEFAULT NULL,
    current_version INT NOT NULL DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_status (user_id, status),
    INDEX idx_user_name (user_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个人数据集';
"""

DDL_USER_DATASET_VERSIONS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_USER_DATASET_VERSIONS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    dataset_id BIGINT NOT NULL,
    version INT NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    row_count INT NOT NULL DEFAULT 0,
    column_count INT NOT NULL DEFAULT 0,
    schema_json JSON DEFAULT NULL,
    missing_stats_json JSON DEFAULT NULL,
    preview_json JSON DEFAULT NULL,
    note VARCHAR(512) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_dataset_version (dataset_id, version),
    INDEX idx_dataset (dataset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据集版本快照';
"""

DDL_USER_MODELS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_USER_MODELS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    model_name VARCHAR(256) NOT NULL,
    framework VARCHAR(64) NOT NULL DEFAULT 'sklearn',
    model_type VARCHAR(64) DEFAULT NULL,
    task_type VARCHAR(64) DEFAULT NULL,
    features JSON DEFAULT NULL,
    metrics JSON DEFAULT NULL,
    params JSON DEFAULT NULL,
    file_path VARCHAR(1024) NOT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'manual',
    source_ref_id BIGINT DEFAULT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_status (user_id, status),
    INDEX idx_source_ref (source, source_ref_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个人模型库';
"""

ALL_RESOURCE_DDLS: List[str] = [
    DDL_USER_FILES,
    DDL_USER_DATASETS,
    DDL_USER_DATASET_VERSIONS,
    DDL_USER_MODELS,
]


def ensure_resource_tables(mysql_handler) -> None:
    """确保个人资源管理相关表存在。"""
    for ddl in ALL_RESOURCE_DDLS:
        mysql_handler.execute(ddl)
