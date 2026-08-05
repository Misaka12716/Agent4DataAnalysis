"""2.1.3 数据质量控制扩展表结构与安全迁移。"""

from __future__ import annotations

from typing import Optional, Tuple


TABLE_CLINICAL_NOTES = "mental_health_clinical_notes"
TABLE_ASSESSMENTS = "mental_health_assessments"
TABLE_MED_ORDERS = "mental_health_med_orders"
TABLE_EXAMINATIONS = "mental_health_examinations"
TABLE_LAB_REPORTS = "mental_health_lab_reports"
TABLE_FOLLOWUPS = "mental_health_followups"
TABLE_MULTIMODAL_ASSETS = "mental_health_multimodal_assets"

OWNER_SCOPED_TABLES = (
    TABLE_CLINICAL_NOTES,
    TABLE_ASSESSMENTS,
    TABLE_MED_ORDERS,
    TABLE_EXAMINATIONS,
    TABLE_LAB_REPORTS,
    TABLE_FOLLOWUPS,
    TABLE_MULTIMODAL_ASSETS,
)

OWNER_PATIENT_INDEX = "idx_dq213_owner_patient"

TABLE_DDLS = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_CLINICAL_NOTES} (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        patient_id VARCHAR(128) NOT NULL,
        owner_user_id BIGINT NOT NULL,
        note_type VARCHAR(64) DEFAULT NULL,
        note_date DATE DEFAULT NULL,
        title VARCHAR(255) DEFAULT NULL,
        content LONGTEXT DEFAULT NULL,
        source VARCHAR(128) DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX {OWNER_PATIENT_INDEX} (owner_user_id, patient_id),
        INDEX idx_dq213_note_date (note_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='2.1.3 临床非结构化文本';
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_ASSESSMENTS} (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        patient_id VARCHAR(128) NOT NULL,
        owner_user_id BIGINT NOT NULL,
        scale_name VARCHAR(128) DEFAULT NULL,
        assess_date DATE DEFAULT NULL,
        total_score FLOAT DEFAULT NULL,
        item_scores JSON DEFAULT NULL,
        rater VARCHAR(128) DEFAULT NULL,
        visit_type VARCHAR(64) DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX {OWNER_PATIENT_INDEX} (owner_user_id, patient_id),
        INDEX idx_dq213_assess_date (assess_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='2.1.3 量表评估记录';
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_MED_ORDERS} (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        patient_id VARCHAR(128) NOT NULL,
        owner_user_id BIGINT NOT NULL,
        drug_name VARCHAR(255) DEFAULT NULL,
        dose VARCHAR(128) DEFAULT NULL,
        frequency VARCHAR(128) DEFAULT NULL,
        route VARCHAR(128) DEFAULT NULL,
        start_date DATE DEFAULT NULL,
        end_date DATE DEFAULT NULL,
        status VARCHAR(64) DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX {OWNER_PATIENT_INDEX} (owner_user_id, patient_id),
        INDEX idx_dq213_med_start_date (start_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='2.1.3 用药医嘱记录';
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_EXAMINATIONS} (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        patient_id VARCHAR(128) NOT NULL,
        owner_user_id BIGINT NOT NULL,
        exam_type VARCHAR(128) DEFAULT NULL,
        exam_date DATE DEFAULT NULL,
        body_site VARCHAR(128) DEFAULT NULL,
        finding LONGTEXT DEFAULT NULL,
        conclusion LONGTEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX {OWNER_PATIENT_INDEX} (owner_user_id, patient_id),
        INDEX idx_dq213_exam_date (exam_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='2.1.3 检查记录';
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_LAB_REPORTS} (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        patient_id VARCHAR(128) NOT NULL,
        owner_user_id BIGINT NOT NULL,
        report_date DATE DEFAULT NULL,
        item_name VARCHAR(255) DEFAULT NULL,
        value_num DOUBLE DEFAULT NULL,
        value_text VARCHAR(255) DEFAULT NULL,
        unit VARCHAR(64) DEFAULT NULL,
        ref_low DOUBLE DEFAULT NULL,
        ref_high DOUBLE DEFAULT NULL,
        flag VARCHAR(64) DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX {OWNER_PATIENT_INDEX} (owner_user_id, patient_id),
        INDEX idx_dq213_lab_date (report_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='2.1.3 检验报告';
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_FOLLOWUPS} (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        patient_id VARCHAR(128) NOT NULL,
        owner_user_id BIGINT NOT NULL,
        visit_date DATE DEFAULT NULL,
        visit_type VARCHAR(64) DEFAULT NULL,
        HAMD_total FLOAT DEFAULT NULL,
        HAMA_total FLOAT DEFAULT NULL,
        PHQ9_total FLOAT DEFAULT NULL,
        medication VARCHAR(255) DEFAULT NULL,
        medication_dose_mg INT DEFAULT NULL,
        adverse_events JSON DEFAULT NULL,
        notes LONGTEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX {OWNER_PATIENT_INDEX} (owner_user_id, patient_id),
        INDEX idx_dq213_followup_date (visit_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='精神科随访记录表';
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_MULTIMODAL_ASSETS} (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        patient_id VARCHAR(128) NOT NULL,
        owner_user_id BIGINT NOT NULL,
        modality VARCHAR(64) NOT NULL,
        mime_type VARCHAR(255) DEFAULT NULL,
        uri VARCHAR(2048) DEFAULT NULL,
        thumbnail_uri VARCHAR(2048) DEFAULT NULL,
        title VARCHAR(255) DEFAULT NULL,
        size_bytes BIGINT DEFAULT NULL,
        checksum VARCHAR(128) DEFAULT NULL,
        event_source_table VARCHAR(128) DEFAULT NULL,
        event_source_id BIGINT DEFAULT NULL,
        metadata_json JSON DEFAULT NULL,
        captured_at DATETIME DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX {OWNER_PATIENT_INDEX} (owner_user_id, patient_id),
        INDEX idx_dq213_asset_captured (captured_at),
        INDEX idx_dq213_asset_source (event_source_table, event_source_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='2.1.3 多模态附件索引';
    """,
)


def _execute(mysql_handler, sql: str) -> Tuple[bool, Optional[str]]:
    _, error = mysql_handler.execute(sql)
    if error:
        return False, str(error)
    return True, None


def _has_column(mysql_handler, table: str, column: str) -> bool:
    rows, error = mysql_handler.query(f"SHOW COLUMNS FROM {table} LIKE %s", (column,))
    return not error and bool(rows)


def _has_index(mysql_handler, table: str, index_name: str) -> bool:
    rows, error = mysql_handler.query(
        f"SHOW INDEX FROM {table} WHERE Key_name=%s",
        (index_name,),
    )
    return not error and bool(rows)


def ensure_dq213_tables(mysql_handler) -> Tuple[bool, Optional[str]]:
    """幂等创建扩展表，并让旧表具备按用户隔离所需字段。"""

    for ddl in TABLE_DDLS:
        ok, error = _execute(mysql_handler, ddl)
        if not ok:
            return False, f"创建 2.1.3 扩展表失败: {error}"

    for table in OWNER_SCOPED_TABLES:
        if not mysql_handler._check_table_exists(table):
            return False, f"2.1.3 扩展表不存在: {table}"
        if not _has_column(mysql_handler, table, "owner_user_id"):
            ok, error = _execute(
                mysql_handler,
                f"ALTER TABLE {table} ADD COLUMN owner_user_id BIGINT DEFAULT NULL AFTER patient_id",
            )
            if not ok:
                return False, f"迁移 {table}.owner_user_id 失败: {error}"
        if not _has_index(mysql_handler, table, OWNER_PATIENT_INDEX):
            ok, error = _execute(
                mysql_handler,
                f"ALTER TABLE {table} ADD INDEX {OWNER_PATIENT_INDEX} (owner_user_id, patient_id)",
            )
            if not ok:
                return False, f"创建 {table} 用户隔离索引失败: {error}"

    return True, None
