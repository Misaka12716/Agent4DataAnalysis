# db/template_schema.py
# 精神疾病分析模板表结构定义（Schema / TypedDict / DDL）

from typing import TypedDict, Optional, List, Any

TABLE_TEMPLATES = "mental_health_templates"

TEMPLATE_COLUMNS = [
    "id",
    "template_name",     # VARCHAR(256) NOT NULL UNIQUE
    "disease_type",      # VARCHAR(64) NOT NULL
    "scales",            # JSON NOT NULL
    "analysis_steps",    # JSON NOT NULL
    "report_structure",  # JSON NOT NULL
    "version",           # VARCHAR(16) NOT NULL DEFAULT '1.0.0'
    "version_history",   # JSON DEFAULT NULL
    "created_at",        # TIMESTAMP
    "updated_at",        # TIMESTAMP
]

VALID_DISEASE_TYPES = [
    "depression",
    "schizophrenia",
    "anxiety",
    "sleep",
    "child_adolescent",
]

class TemplateRow(TypedDict, total=False):
    id: int
    template_name: str
    disease_type: str
    scales: List[str]
    analysis_steps: List[dict]
    report_structure: List[str]
    version: str
    version_history: Optional[List[dict]]
    created_at: Optional[str]
    updated_at: Optional[str]

TEMPLATE_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_TEMPLATES} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    template_name VARCHAR(256) NOT NULL UNIQUE COMMENT '模板名称',
    disease_type VARCHAR(64) NOT NULL COMMENT '专病类型',
    scales JSON NOT NULL COMMENT '症状量表清单',
    analysis_steps JSON NOT NULL COMMENT '分析步骤定义',
    report_structure JSON NOT NULL COMMENT '报告章节结构',
    version VARCHAR(16) NOT NULL DEFAULT '1.0.0' COMMENT '语义版本号',
    version_history JSON DEFAULT NULL COMMENT '历史版本快照',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '精神疾病分析模板表';
"""
