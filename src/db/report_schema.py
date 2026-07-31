# db/report_schema.py
# 精神科图文报告存储表

from typing import TypedDict, Optional, List, Any

TABLE_REPORTS = "mental_health_reports"

REPORT_COLUMNS = [
    "id",
    "user_id",           # BIGINT
    "session_id",        # VARCHAR(64)
    "template_id",       # BIGINT
    "report_name",       # VARCHAR(256)
    "report_json",       # LONGTEXT — 结构化报告 JSON
    "html_content",      # LONGTEXT — 渲染后的 HTML
    "pdf_path",          # VARCHAR(512) — PDF 文件路径
    "sections",          # JSON — 章节列表
    "created_at",
    "updated_at",
]

class ReportRow(TypedDict, total=False):
    id: int
    user_id: int
    session_id: Optional[str]
    template_id: Optional[int]
    report_name: str
    report_json: Optional[str]
    html_content: Optional[str]
    pdf_path: Optional[str]
    sections: Optional[list]
    created_at: Optional[str]
    updated_at: Optional[str]

REPORT_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_REPORTS} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '用户ID',
    session_id VARCHAR(64) DEFAULT NULL COMMENT '会话ID',
    template_id BIGINT DEFAULT NULL COMMENT '模板ID',
    report_name VARCHAR(256) NOT NULL COMMENT '报告名称',
    report_json LONGTEXT DEFAULT NULL COMMENT '结构化报告 JSON',
    html_content LONGTEXT DEFAULT NULL COMMENT 'HTML 内容',
    pdf_path VARCHAR(512) DEFAULT NULL COMMENT 'PDF 文件路径',
    sections JSON DEFAULT NULL COMMENT '章节列表',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '精神科图文报告表';
"""
