# db/reference_schema.py
# 参考区间表结构定义

from typing import TypedDict, Optional, List

TABLE_REFERENCE_RANGES = "mental_health_reference_ranges"

REFERENCE_RANGE_COLUMNS = [
    "id",
    "indicator",         # VARCHAR(128) NOT NULL  — 指标名称
    "gender",            # VARCHAR(8) DEFAULT NULL  — 性别分层
    "age_range_lower",   # INT DEFAULT NULL
    "age_range_upper",   # INT DEFAULT NULL
    "diagnosis",         # VARCHAR(128) DEFAULT NULL  — 诊断分层
    "lower_bound",       # FLOAT NOT NULL
    "upper_bound",       # FLOAT NOT NULL
    "unit",              # VARCHAR(64) DEFAULT NULL
    "source",            # VARCHAR(256) DEFAULT NULL  — 来源
    "created_at",
    "updated_at",
]

class ReferenceRangeRow(TypedDict, total=False):
    id: int
    indicator: str
    gender: Optional[str]
    age_range_lower: Optional[int]
    age_range_upper: Optional[int]
    diagnosis: Optional[str]
    lower_bound: float
    upper_bound: float
    unit: Optional[str]
    source: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

REFERENCE_RANGE_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_REFERENCE_RANGES} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    indicator VARCHAR(128) NOT NULL COMMENT '指标名称',
    gender VARCHAR(8) DEFAULT NULL COMMENT '性别分层',
    age_range_lower INT DEFAULT NULL COMMENT '年龄下限',
    age_range_upper INT DEFAULT NULL COMMENT '年龄上限',
    diagnosis VARCHAR(128) DEFAULT NULL COMMENT '诊断分层',
    lower_bound FLOAT NOT NULL COMMENT '参考下限',
    upper_bound FLOAT NOT NULL COMMENT '参考上限',
    unit VARCHAR(64) DEFAULT NULL COMMENT '单位',
    source TEXT DEFAULT NULL COMMENT '数据来源',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_indicator (indicator)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '精神科指标参考区间表';
"""
