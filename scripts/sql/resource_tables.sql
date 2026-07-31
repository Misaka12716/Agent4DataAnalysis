-- 个人资源管理建表 DDL（可手工在 MySQL 执行）
-- 库：agent_platform（与 configs/config.py / .env 一致）
-- 也可由后端首次调用资源 API 时自动 ensure_resource_tables

CREATE TABLE IF NOT EXISTS user_files (
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

CREATE TABLE IF NOT EXISTS user_datasets (
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

CREATE TABLE IF NOT EXISTS user_dataset_versions (
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

CREATE TABLE IF NOT EXISTS user_models (
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
