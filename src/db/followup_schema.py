# db/followup_schema.py
# 随访记录表结构定义

from typing import TypedDict, Optional, List, Any

TABLE_FOLLOWUPS = "mental_health_followups"

FOLLOWUP_COLUMNS = [
    "id",
    "patient_id",        # VARCHAR(32) NOT NULL
    "visit_date",        # DATE NOT NULL
    "visit_type",        # VARCHAR(32) — baseline/week4/week8/week12/custom
    "HAMD_total",        # FLOAT
    "HAMA_total",        # FLOAT
    "PHQ9_total",        # FLOAT
    "medication",        # VARCHAR(256)
    "medication_dose_mg", # INT
    "adverse_events",    # JSON
    "notes",             # TEXT
    "created_at",        # TIMESTAMP
    "updated_at",        # TIMESTAMP
]

class FollowupRow(TypedDict, total=False):
    id: int
    patient_id: str
    visit_date: str
    visit_type: Optional[str]
    HAMD_total: Optional[float]
    HAMA_total: Optional[float]
    PHQ9_total: Optional[float]
    medication: Optional[str]
    medication_dose_mg: Optional[int]
    adverse_events: Optional[list]
    notes: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

FOLLOWUP_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_FOLLOWUPS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    patient_id VARCHAR(32) NOT NULL COMMENT '患者编号',
    owner_user_id BIGINT DEFAULT NULL COMMENT '数据归属用户',
    visit_date DATE NOT NULL COMMENT '随访日期',
    visit_type VARCHAR(32) DEFAULT NULL COMMENT '随访类型',
    HAMD_total FLOAT DEFAULT NULL COMMENT 'HAMD 总分',
    HAMA_total FLOAT DEFAULT NULL COMMENT 'HAMA 总分',
    PHQ9_total FLOAT DEFAULT NULL COMMENT 'PHQ-9 总分',
    medication VARCHAR(256) DEFAULT NULL COMMENT '用药信息',
    medication_dose_mg INT DEFAULT NULL COMMENT '药物剂量',
    adverse_events JSON DEFAULT NULL COMMENT '不良事件',
    notes TEXT DEFAULT NULL COMMENT '备注',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_patient_id (patient_id),
    INDEX idx_visit_date (visit_date),
    INDEX idx_owner_user_id (owner_user_id),
    UNIQUE KEY uk_patient_visit_owner (patient_id, visit_date, owner_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '精神科随访记录表';
"""
