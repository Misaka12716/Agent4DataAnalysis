# db/patient_schema.py
# 精神疾病患者表 + 查询条件保存表 结构定义

from typing import TypedDict, Optional, List, Any

TABLE_PATIENTS = "mental_health_patients"
TABLE_PATIENT_QUERIES = "mental_health_patient_queries"

PATIENT_COLUMNS = [
    "id",
    "patient_id",           # VARCHAR(32) NOT NULL — 与 owner_user_id 联合唯一
    "owner_user_id",        # BIGINT — 数据归属用户
    "age",                  # INT
    "gender",               # VARCHAR(8)
    "diagnosis",            # VARCHAR(128)
    "admission_date",       # DATE
    "discharge_date",       # DATE
    "HAMD_total",           # FLOAT
    "HAMA_total",           # FLOAT
    "PHQ9_total",           # FLOAT
    "disease_duration_years",  # FLOAT — 病程（年），训练/批量预测常用
    "medication",           # VARCHAR(256)
    "outcome",              # VARCHAR(32)
    "relapse",              # TINYINT
    "created_at",           # TIMESTAMP
    "updated_at",           # TIMESTAMP
]

PATIENT_QUERY_COLUMNS = [
    "id",
    "user_id",              # BIGINT
    "query_name",           # VARCHAR(256)
    "condition_tree",       # JSON — 条件树
    "created_at",
]

ALLOWED_PATIENT_FIELDS = [
    "patient_id", "age", "gender", "diagnosis",
    "admission_date", "discharge_date",
    "HAMD_total", "HAMA_total", "PHQ9_total",
    "disease_duration_years",
    "medication", "outcome", "relapse",
]

ALLOWED_OPERATORS = ["=", "!=", ">", "<", ">=", "<=", "IN", "NOT IN", "LIKE", "BETWEEN"]

class PatientRow(TypedDict, total=False):
    id: int
    patient_id: str
    age: int
    gender: str
    diagnosis: str
    admission_date: Optional[str]
    discharge_date: Optional[str]
    HAMD_total: Optional[float]
    HAMA_total: Optional[float]
    PHQ9_total: Optional[float]
    disease_duration_years: Optional[float]
    medication: Optional[str]
    outcome: Optional[str]
    relapse: Optional[int]
    created_at: Optional[str]
    updated_at: Optional[str]

class PatientQueryRow(TypedDict, total=False):
    id: int
    user_id: int
    query_name: str
    condition_tree: dict
    created_at: Optional[str]

PATIENT_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PATIENTS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    patient_id VARCHAR(32) NOT NULL COMMENT '患者编号',
    owner_user_id BIGINT DEFAULT NULL COMMENT '数据归属用户',
    age INT DEFAULT NULL COMMENT '年龄',
    gender VARCHAR(8) DEFAULT NULL COMMENT '性别',
    diagnosis VARCHAR(128) DEFAULT NULL COMMENT '主要诊断',
    admission_date DATE DEFAULT NULL COMMENT '入院日期',
    discharge_date DATE DEFAULT NULL COMMENT '出院日期',
    HAMD_total FLOAT DEFAULT NULL COMMENT 'HAMD 总分',
    HAMA_total FLOAT DEFAULT NULL COMMENT 'HAMA 总分',
    PHQ9_total FLOAT DEFAULT NULL COMMENT 'PHQ-9 总分',
    disease_duration_years FLOAT DEFAULT NULL COMMENT '病程（年）',
    medication VARCHAR(256) DEFAULT NULL COMMENT '用药信息',
    outcome VARCHAR(32) DEFAULT NULL COMMENT '治疗结局',
    relapse TINYINT DEFAULT 0 COMMENT '是否复发',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_patient_owner (patient_id, owner_user_id),
    INDEX idx_diagnosis (diagnosis),
    INDEX idx_age (age),
    INDEX idx_owner_user_id (owner_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '精神疾病患者表';
"""

PATIENT_QUERY_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PATIENT_QUERIES} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '用户ID',
    query_name VARCHAR(256) NOT NULL COMMENT '查询条件名称',
    condition_tree JSON NOT NULL COMMENT '条件树 JSON',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '患者查询条件保存表';
"""
