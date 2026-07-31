# db/risk_schema.py
# 风险预测模型 + 预测记录表

from typing import TypedDict, Optional, List, Any

TABLE_RISK_MODELS = "mental_health_risk_models"
TABLE_PREDICTIONS = "mental_health_predictions"

RISK_MODEL_COLUMNS = [
    "id",
    "model_name",        # VARCHAR(256)
    "task_type",         # VARCHAR(64) — relapse/self_harm/adverse_reaction
    "model_type",        # VARCHAR(64) — LogisticRegression/RandomForest
    "features",          # JSON — 特征列表
    "metrics",           # JSON — {AUC, accuracy, F1, ...}
    "params",            # JSON — 模型超参数
    "file_path",         # VARCHAR(512) — 模型文件路径（按用户分目录）
    "owner_user_id",     # BIGINT — 所属用户
    "created_at",
    "updated_at",
]

PREDICTION_COLUMNS = [
    "id",
    "model_id",          # BIGINT
    "patient_id",        # VARCHAR(32)
    "risk_score",        # FLOAT
    "risk_level",        # VARCHAR(16) — low/medium/high/critical
    "prediction_label",  # INT
    "feature_contributions", # JSON
    "owner_user_id",     # BIGINT — 所属用户
    "created_at",
]

class RiskModelRow(TypedDict, total=False):
    id: int
    model_name: str
    task_type: str
    model_type: str
    features: List[str]
    metrics: dict
    params: dict
    file_path: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

class PredictionRow(TypedDict, total=False):
    id: int
    model_id: int
    patient_id: str
    risk_score: float
    risk_level: str
    prediction_label: int
    feature_contributions: Optional[list]
    created_at: Optional[str]

RISK_MODEL_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_RISK_MODELS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    model_name VARCHAR(256) NOT NULL COMMENT '模型名称',
    task_type VARCHAR(64) NOT NULL COMMENT '预测任务类型',
    model_type VARCHAR(64) NOT NULL COMMENT '模型算法类型',
    features JSON NOT NULL COMMENT '特征列表',
    metrics JSON NOT NULL COMMENT '性能指标',
    params JSON DEFAULT NULL COMMENT '超参数',
    file_path VARCHAR(512) DEFAULT NULL COMMENT '模型文件路径',
    owner_user_id BIGINT DEFAULT NULL COMMENT '所属用户',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_owner_user_id (owner_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '精神科风险预测模型表';
"""

PREDICTION_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PREDICTIONS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    model_id BIGINT NOT NULL COMMENT '模型ID',
    patient_id VARCHAR(32) NOT NULL COMMENT '患者编号',
    risk_score FLOAT NOT NULL COMMENT '风险概率',
    risk_level VARCHAR(16) NOT NULL COMMENT '风险等级',
    prediction_label INT DEFAULT NULL COMMENT '预测标签',
    feature_contributions JSON DEFAULT NULL COMMENT '特征贡献度',
    owner_user_id BIGINT DEFAULT NULL COMMENT '所属用户',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_model_id (model_id),
    INDEX idx_patient_id (patient_id),
    INDEX idx_owner_user_id (owner_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '精神科风险预测记录表';
"""
