# db/comorbidity_schema.py
# 共病分析记录表

from typing import TypedDict, Optional, List, Any

TABLE_COMORBIDITY = "mental_health_comorbidity_analysis"

COMORBIDITY_COLUMNS = [
    "id",
    "analysis_name",     # VARCHAR(256)
    "cohort_ids",        # JSON — 患者 ID 列表
    "matrix_data",       # JSON — 共病矩阵
    "spectrum_data",     # JSON — 谱系关系
    "cluster_data",      # JSON — 聚类结果
    "heatmap_json",      # JSON — 热图数据
    "network_json",      # JSON — 网络图数据
    "owner_user_id",     # BIGINT — 所属用户
    "created_at",
]

class ComorbidityRow(TypedDict, total=False):
    id: int
    analysis_name: str
    cohort_ids: list
    matrix_data: dict
    spectrum_data: dict
    cluster_data: dict
    heatmap_json: dict
    network_json: dict
    created_at: Optional[str]

COMORBIDITY_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_COMORBIDITY} (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    analysis_name VARCHAR(256) NOT NULL COMMENT '分析名称',
    cohort_ids JSON DEFAULT NULL COMMENT '患者 ID 列表',
    matrix_data JSON DEFAULT NULL COMMENT '共病矩阵',
    spectrum_data JSON DEFAULT NULL COMMENT '谱系关系数据',
    cluster_data JSON DEFAULT NULL COMMENT '聚类结果',
    heatmap_json JSON DEFAULT NULL COMMENT '热图渲染数据',
    network_json JSON DEFAULT NULL COMMENT '网络图渲染数据',
    owner_user_id BIGINT DEFAULT NULL COMMENT '所属用户',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_owner_user_id (owner_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '共病分析记录表';
"""
