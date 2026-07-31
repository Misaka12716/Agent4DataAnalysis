# backend/clinical_owner.py — 临床库按用户隔离（owner_user_id）

from __future__ import annotations

from typing import List, Optional, Tuple

from utils.mysql_utils import mysql_handler

OWNER_COLUMN = "owner_user_id"

TABLES_WITH_OWNER = (
    "mental_health_patients",
    "mental_health_followups",
    "mental_health_reference_ranges",
)

# 仅需补齐 owner_user_id 列（无复合唯一键迁移）
TABLES_WITH_OWNER_COLUMN = (
    "mental_health_risk_models",
    "mental_health_predictions",
    "mental_health_comorbidity_analysis",
)

PATIENT_OWNER_UNIQUE = "uk_patient_owner"
FOLLOWUP_OWNER_UNIQUE = "uk_patient_visit_owner"
LEGACY_PATIENT_UNIQUE = "patient_id"
LEGACY_FOLLOWUP_UNIQUE = "uk_patient_visit"


def _table_has_column(table: str, column: str) -> bool:
    if not mysql_handler._check_table_exists(table):
        return False
    rows, _ = mysql_handler.query(f"SHOW COLUMNS FROM {table} LIKE %s", (column,))
    return bool(rows)


def _table_has_index(table: str, index_name: str) -> bool:
    if not mysql_handler._check_table_exists(table):
        return False
    rows, _ = mysql_handler.query(f"SHOW INDEX FROM {table} WHERE Key_name = %s", (index_name,))
    return bool(rows)


def migrate_owner_column(table: str) -> Tuple[bool, Optional[str]]:
    """为已有表补齐 owner_user_id 列（幂等）。"""
    if not mysql_handler._check_table_exists(table):
        return True, None
    if _table_has_column(table, OWNER_COLUMN):
        return True, None
    _, err = mysql_handler.execute(
        f"ALTER TABLE {table} ADD COLUMN {OWNER_COLUMN} BIGINT DEFAULT NULL"
    )
    if err and "duplicate" not in str(err).lower() and "exists" not in str(err).lower():
        return False, f"迁移 {table}.{OWNER_COLUMN} 失败: {err}"
    return True, None


def migrate_patient_owner_unique_key() -> Tuple[bool, Optional[str]]:
    """患者表唯一键从 patient_id 改为 (patient_id, owner_user_id)。"""
    table = TABLES_WITH_OWNER[0]
    if not mysql_handler._check_table_exists(table):
        return True, None
    ok, err = migrate_owner_column(table)
    if not ok:
        return False, err
    if _table_has_index(table, PATIENT_OWNER_UNIQUE):
        return True, None
    if _table_has_index(table, LEGACY_PATIENT_UNIQUE):
        _, err = mysql_handler.execute(f"ALTER TABLE {table} DROP INDEX {LEGACY_PATIENT_UNIQUE}")
        if err and "check that column/key exists" not in str(err).lower():
            return False, f"删除旧唯一键 {LEGACY_PATIENT_UNIQUE} 失败: {err}"
    _, err = mysql_handler.execute(
        f"ALTER TABLE {table} ADD UNIQUE KEY {PATIENT_OWNER_UNIQUE} (patient_id, {OWNER_COLUMN})"
    )
    if err and "duplicate" not in str(err).lower() and "exists" not in str(err).lower():
        return False, f"创建 {PATIENT_OWNER_UNIQUE} 失败: {err}"
    return True, None


def migrate_followup_owner_unique_key() -> Tuple[bool, Optional[str]]:
    """随访表唯一键从 (patient_id, visit_date) 改为含 owner_user_id。"""
    table = TABLES_WITH_OWNER[1]
    if not mysql_handler._check_table_exists(table):
        return True, None
    ok, err = migrate_owner_column(table)
    if not ok:
        return False, err
    if _table_has_index(table, FOLLOWUP_OWNER_UNIQUE):
        return True, None
    if _table_has_index(table, LEGACY_FOLLOWUP_UNIQUE):
        _, err = mysql_handler.execute(f"ALTER TABLE {table} DROP INDEX {LEGACY_FOLLOWUP_UNIQUE}")
        if err and "check that column/key exists" not in str(err).lower():
            return False, f"删除旧唯一键 {LEGACY_FOLLOWUP_UNIQUE} 失败: {err}"
    _, err = mysql_handler.execute(
        f"ALTER TABLE {table} ADD UNIQUE KEY {FOLLOWUP_OWNER_UNIQUE} "
        f"(patient_id, visit_date, {OWNER_COLUMN})"
    )
    if err and "duplicate" not in str(err).lower() and "exists" not in str(err).lower():
        return False, f"创建 {FOLLOWUP_OWNER_UNIQUE} 失败: {err}"
    return True, None


def migrate_all_clinical_owner_columns() -> Tuple[bool, Optional[str]]:
    for table in TABLES_WITH_OWNER:
        ok, err = migrate_owner_column(table)
        if not ok:
            return False, err
    ok, err = migrate_patient_owner_unique_key()
    if not ok:
        return False, err
    ok, err = migrate_followup_owner_unique_key()
    if not ok:
        return False, err
    for table in TABLES_WITH_OWNER_COLUMN:
        ok, err = migrate_owner_column(table)
        if not ok:
            return False, err
    return True, None


def owner_scope_sql(user_id: int, alias: str = "") -> Tuple[str, List[int]]:
    col = f"{alias}.{OWNER_COLUMN}" if alias else OWNER_COLUMN
    return f"{col} = %s", [int(user_id)]


def count_for_owner(table: str, user_id: int) -> int:
    if not user_id or not mysql_handler._check_table_exists(table):
        return 0
    clause, params = owner_scope_sql(int(user_id))
    rows, err = mysql_handler.query(f"SELECT COUNT(*) AS c FROM {table} WHERE {clause}", tuple(params))
    if err or not rows:
        return 0
    return int(rows[0].get("c") or 0)
