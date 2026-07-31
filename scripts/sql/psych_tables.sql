-- 精神专科多维度分析域 DDL（运维手工执行可选；API 首次调用也会 ensure）
-- 完整权威定义见 src/db/psych_schema.py

CREATE TABLE IF NOT EXISTS psych_datasets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    project_id INT DEFAULT NULL,
    name VARCHAR(256) NOT NULL,
    source_type VARCHAR(64) NOT NULL DEFAULT 'mixed',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    schema_json JSON,
    row_count INT DEFAULT 0,
    file_path VARCHAR(1024) DEFAULT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_psych_ds_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS psych_tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL UNIQUE,
    user_id INT NOT NULL,
    module VARCHAR(64) NOT NULL,
    method_id VARCHAR(256) DEFAULT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    params_json JSON,
    result_json JSON,
    artifact_path VARCHAR(1024) DEFAULT NULL,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP NULL,
    INDEX idx_psych_task_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS psych_stats_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    method_id VARCHAR(128) NOT NULL,
    summary_json JSON,
    tables_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_stats_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS psych_ml_models (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    task_id VARCHAR(64) DEFAULT NULL,
    algo_id VARCHAR(128) NOT NULL,
    model_name VARCHAR(256) NOT NULL,
    metrics_json JSON,
    feature_list_json JSON,
    model_path VARCHAR(1024) DEFAULT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_ml_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS psych_capabilities (
    id INT AUTO_INCREMENT PRIMARY KEY,
    capability_id VARCHAR(128) NOT NULL UNIQUE,
    kind VARCHAR(64) NOT NULL,
    impl_ref VARCHAR(256) NOT NULL,
    version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    meta_json JSON,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 其余表见 src/db/psych_schema.py
