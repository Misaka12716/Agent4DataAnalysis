# backend/clinical_data_service.py — 临床库数据状态与队列解析（按用户隔离）

from __future__ import annotations

from typing import List, Optional, Tuple

from utils.mysql_utils import mysql_handler
from db.patient_schema import TABLE_PATIENTS
from db.followup_schema import TABLE_FOLLOWUPS
from db.reference_schema import TABLE_REFERENCE_RANGES
from db.risk_schema import TABLE_RISK_MODELS, TABLE_PREDICTIONS


def get_clinical_data_status(user_id: int) -> Tuple[dict, Optional[str]]:
    """返回当前用户上传的患者/随访/参考区间记录数。"""
    from backend.clinical_owner import count_for_owner, migrate_all_clinical_owner_columns

    status = {
        "patients": 0,
        "followups": 0,
        "reference_ranges": 0,
        "risk_models": 0,
        "predictions": 0,
        "ready": False,
    }
    if not user_id or int(user_id) <= 0:
        return status, None
    try:
        ok, err = migrate_all_clinical_owner_columns()
        if not ok:
            return status, err
        uid = int(user_id)
        status["patients"] = count_for_owner(TABLE_PATIENTS, uid)
        status["followups"] = count_for_owner(TABLE_FOLLOWUPS, uid)
        status["reference_ranges"] = count_for_owner(TABLE_REFERENCE_RANGES, uid)
        status["risk_models"] = count_for_owner(TABLE_RISK_MODELS, uid)
        status["predictions"] = count_for_owner(TABLE_PREDICTIONS, uid)
        status["ready"] = status["patients"] > 0
        return status, None
    except Exception as e:
        return status, str(e)


def list_patient_ids(user_id: int, limit: int = 50) -> List[str]:
    if not user_id or not mysql_handler._check_table_exists(TABLE_PATIENTS):
        return []
    from backend.clinical_owner import owner_scope_sql

    clause, params = owner_scope_sql(int(user_id))
    rows, _ = mysql_handler.query(
        f"SELECT patient_id FROM {TABLE_PATIENTS} WHERE {clause} ORDER BY patient_id LIMIT {int(limit)}",
        tuple(params),
    )
    return [str(r["patient_id"]) for r in (rows or []) if r.get("patient_id")]


def resolve_cohort_ids(
    cohort_patient_ids: Optional[List[str]] = None,
    limit: int = 30,
    owner_user_id: Optional[int] = None,
) -> List[str]:
    """报告/自动汇总用：优先用前端传入队列，否则取当前用户库内患者。"""
    if cohort_patient_ids:
        return [str(x).strip() for x in cohort_patient_ids if str(x).strip()][:limit]
    if owner_user_id:
        return list_patient_ids(int(owner_user_id), limit)
    return []


def get_patient_row(patient_id: str, owner_user_id: int) -> Optional[dict]:
    """按 patient_id 读取当前用户拥有的患者行。"""
    pid = str(patient_id or "").strip()
    if not pid or not owner_user_id:
        return None
    from backend.clinical_owner import owner_scope_sql

    clause, params = owner_scope_sql(int(owner_user_id))
    rows, _ = mysql_handler.query(
        f"SELECT * FROM {TABLE_PATIENTS} WHERE patient_id = %s AND {clause} LIMIT 1",
        (pid,) + tuple(params),
    )
    return rows[0] if rows else None


def fetch_patient_rows(
    patient_ids: Optional[List[str]],
    owner_user_id: int,
    limit: int = 500,
) -> List[dict]:
    """批量读取当前用户患者（可空 patient_ids 表示库内全部）。"""
    if not owner_user_id:
        return []
    from backend.clinical_owner import owner_scope_sql

    clause, params = owner_scope_sql(int(owner_user_id))
    if patient_ids:
        ids = [str(x).strip() for x in patient_ids if str(x).strip()][:limit]
        if not ids:
            return []
        placeholders = ", ".join(["%s"] * len(ids))
        rows, _ = mysql_handler.query(
            f"SELECT * FROM {TABLE_PATIENTS} WHERE patient_id IN ({placeholders}) AND {clause}",
            tuple(ids) + tuple(params),
        )
        return list(rows or [])
    rows, _ = mysql_handler.query(
        f"SELECT * FROM {TABLE_PATIENTS} WHERE {clause} ORDER BY patient_id LIMIT {int(limit)}",
        tuple(params),
    )
    return list(rows or [])
