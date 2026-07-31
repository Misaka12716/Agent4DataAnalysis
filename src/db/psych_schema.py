# db/psych_schema.py
# 精神专科多维度分析域表结构

from __future__ import annotations

TABLE_PSYCH_DATASETS = "psych_datasets"
TABLE_PSYCH_DATA_RECORDS = "psych_data_records"
TABLE_PSYCH_INGEST_JOBS = "psych_ingest_jobs"
TABLE_PSYCH_VARIABLES = "psych_variables"
TABLE_PSYCH_VAR_CATEGORIES = "psych_var_categories"
TABLE_PSYCH_PARAM_TEMPLATES = "psych_param_templates"
TABLE_PSYCH_ANALYSIS_PARAMS = "psych_analysis_params"
TABLE_PSYCH_TASKS = "psych_tasks"
TABLE_PSYCH_STATS_RESULTS = "psych_stats_results"
TABLE_PSYCH_ML_MODELS = "psych_ml_models"
TABLE_PSYCH_FEATURES = "psych_features"
TABLE_PSYCH_SCALE_FORMS = "psych_scale_forms"
TABLE_PSYCH_SCALE_SCORES = "psych_scale_scores"
TABLE_PSYCH_LLM_EXTRACTIONS = "psych_llm_extractions"
TABLE_PSYCH_EXPORTS = "psych_exports"
TABLE_PSYCH_CAPABILITIES = "psych_capabilities"
TABLE_PSYCH_PIPELINES = "psych_pipelines"
TABLE_PSYCH_CAPABILITY_CHANGELOG = "psych_capability_changelog"

DDL_PSYCH_DATASETS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_DATASETS} (
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
    INDEX idx_psych_ds_user (user_id),
    INDEX idx_psych_ds_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='精神专科一体化数据集';
"""

DDL_PSYCH_DATA_RECORDS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_DATA_RECORDS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    dataset_id INT NOT NULL,
    record_type VARCHAR(64) NOT NULL DEFAULT 'row',
    patient_key VARCHAR(128) DEFAULT NULL,
    record_time DATETIME DEFAULT NULL,
    payload_path VARCHAR(1024) DEFAULT NULL,
    tags_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_rec_ds (dataset_id),
    INDEX idx_psych_rec_patient (patient_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准化记录索引';
"""

DDL_PSYCH_INGEST_JOBS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_INGEST_JOBS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    job_id VARCHAR(64) NOT NULL UNIQUE,
    dataset_id INT NOT NULL,
    user_id INT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    stats_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP NULL,
    INDEX idx_psych_ingest_ds (dataset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据接入解析任务';
"""

DDL_PSYCH_VARIABLES = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_VARIABLES} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    dataset_id INT DEFAULT NULL,
    user_id INT NOT NULL,
    var_name VARCHAR(256) NOT NULL,
    display_name VARCHAR(256) DEFAULT NULL,
    category VARCHAR(128) DEFAULT NULL,
    dtype VARCHAR(64) DEFAULT NULL,
    dict_code VARCHAR(128) DEFAULT NULL,
    mapping_json JSON,
    relations_json JSON,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_psych_var_user (user_id),
    INDEX idx_psych_var_ds (dataset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='变量定义';
"""

DDL_PSYCH_VAR_CATEGORIES = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_VAR_CATEGORIES} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(256) NOT NULL,
    parent_id INT DEFAULT NULL,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_varcat_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='变量分类树';
"""

DDL_PSYCH_PARAM_TEMPLATES = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_PARAM_TEMPLATES} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    module VARCHAR(64) NOT NULL,
    method_id VARCHAR(128) NOT NULL,
    name VARCHAR(256) NOT NULL,
    params_json JSON,
    is_default TINYINT(1) DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_psych_tpl_user (user_id),
    INDEX idx_psych_tpl_module (module, method_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分析参数模板';
"""

DDL_PSYCH_ANALYSIS_PARAMS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_ANALYSIS_PARAMS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    scope VARCHAR(64) NOT NULL,
    param_key VARCHAR(128) NOT NULL,
    value_json JSON,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_psych_param (user_id, scope, param_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质控/统计/解析参数';
"""

DDL_PSYCH_TASKS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_TASKS} (
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
    INDEX idx_psych_task_user (user_id),
    INDEX idx_psych_task_module (module),
    INDEX idx_psych_task_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='统一异步任务';
"""

DDL_PSYCH_STATS_RESULTS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_STATS_RESULTS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    method_id VARCHAR(128) NOT NULL,
    summary_json JSON,
    tables_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_stats_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='一键统计结果';
"""

DDL_PSYCH_ML_MODELS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_ML_MODELS} (
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
    INDEX idx_psych_ml_user (user_id),
    INDEX idx_psych_ml_algo (algo_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='精神专科ML模型登记';
"""

DDL_PSYCH_FEATURES = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_FEATURES} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    dataset_id INT DEFAULT NULL,
    feature_set_name VARCHAR(256) NOT NULL,
    feature_type VARCHAR(64) NOT NULL,
    feature_matrix_path VARCHAR(1024) DEFAULT NULL,
    meta_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_feat_user (user_id),
    INDEX idx_psych_feat_ds (dataset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='特征挖掘结果';
"""

DDL_PSYCH_SCALE_FORMS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_SCALE_FORMS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    scale_code VARCHAR(64) NOT NULL,
    version VARCHAR(32) NOT NULL DEFAULT '1.0',
    display_name VARCHAR(256) DEFAULT NULL,
    items_json JSON,
    scoring_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_psych_scale (scale_code, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量表定义';
"""

DDL_PSYCH_SCALE_SCORES = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_SCALE_SCORES} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    dataset_id INT DEFAULT NULL,
    patient_key VARCHAR(128) NOT NULL,
    scale_code VARCHAR(64) NOT NULL,
    item_scores_json JSON,
    total FLOAT DEFAULT NULL,
    subscales_json JSON,
    scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_score_user (user_id),
    INDEX idx_psych_score_patient (patient_key),
    INDEX idx_psych_score_scale (scale_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量表结构化得分';
"""

DDL_PSYCH_LLM_EXTRACTIONS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_LLM_EXTRACTIONS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    dataset_id INT DEFAULT NULL,
    record_id INT DEFAULT NULL,
    extract_type VARCHAR(64) NOT NULL,
    result_json JSON,
    model_name VARCHAR(128) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_llm_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LLM抽取结果';
"""

DDL_PSYCH_EXPORTS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_EXPORTS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    export_id VARCHAR(64) NOT NULL UNIQUE,
    user_id INT NOT NULL,
    task_id VARCHAR(64) DEFAULT NULL,
    kind VARCHAR(64) NOT NULL,
    format VARCHAR(32) NOT NULL,
    file_path VARCHAR(1024) DEFAULT NULL,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_export_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='导出记录';
"""

DDL_PSYCH_CAPABILITIES = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_CAPABILITIES} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    capability_id VARCHAR(128) NOT NULL UNIQUE,
    kind VARCHAR(64) NOT NULL,
    impl_ref VARCHAR(256) NOT NULL,
    version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    meta_json JSON,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_psych_cap_kind (kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='算法/LLM能力注册';
"""

DDL_PSYCH_PIPELINES = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_PIPELINES} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(256) NOT NULL,
    steps_json JSON,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_psych_pipe_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分析管线编排';
"""

DDL_PSYCH_CAPABILITY_CHANGELOG = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PSYCH_CAPABILITY_CHANGELOG} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    capability_id VARCHAR(128) NOT NULL,
    from_ver VARCHAR(32) DEFAULT NULL,
    to_ver VARCHAR(32) NOT NULL,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_psych_caplog (capability_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='能力升级审计';
"""

ALL_PSYCH_DDLS = (
    DDL_PSYCH_DATASETS,
    DDL_PSYCH_DATA_RECORDS,
    DDL_PSYCH_INGEST_JOBS,
    DDL_PSYCH_VARIABLES,
    DDL_PSYCH_VAR_CATEGORIES,
    DDL_PSYCH_PARAM_TEMPLATES,
    DDL_PSYCH_ANALYSIS_PARAMS,
    DDL_PSYCH_TASKS,
    DDL_PSYCH_STATS_RESULTS,
    DDL_PSYCH_ML_MODELS,
    DDL_PSYCH_FEATURES,
    DDL_PSYCH_SCALE_FORMS,
    DDL_PSYCH_SCALE_SCORES,
    DDL_PSYCH_LLM_EXTRACTIONS,
    DDL_PSYCH_EXPORTS,
    DDL_PSYCH_CAPABILITIES,
    DDL_PSYCH_PIPELINES,
    DDL_PSYCH_CAPABILITY_CHANGELOG,
)


def ensure_psych_tables(mysql_handler) -> None:
    for ddl in ALL_PSYCH_DDLS:
        mysql_handler.execute(ddl)
