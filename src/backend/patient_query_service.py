# backend/patient_query_service.py
# 临床数据智能检索与纳排 — 动态 SQL 构建 + 查询 + 保存 + 导出

import json
from typing import Any, Dict, List, Optional, Tuple

from utils.mysql_utils import mysql_handler
from db.patient_schema import (
    TABLE_PATIENTS,
    TABLE_PATIENT_QUERIES,
    ALLOWED_PATIENT_FIELDS,
    ALLOWED_OPERATORS,
)


def _norm_date_field(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        if "T" in v:
            return v.split("T", 1)[0]
        if " " in v:
            return v.split(" ", 1)[0]
        return v[:10] if len(v) >= 10 else v
    s = str(value).strip()
    if " " in s:
        return s.split(" ", 1)[0]
    return s[:10] if len(s) >= 10 else s or None


def _ensure_tables() -> Tuple[bool, Optional[str]]:
    """确保患者表与查询条件表存在。"""
    try:
        for table, ddl in [
            (TABLE_PATIENTS, None),
            (TABLE_PATIENT_QUERIES, None),
        ]:
            if not mysql_handler._check_table_exists(table):
                if table == TABLE_PATIENTS:
                    from db.patient_schema import PATIENT_TABLE_DDL as ddl
                else:
                    from db.patient_schema import PATIENT_QUERY_TABLE_DDL as ddl
                affected, err = mysql_handler.execute(ddl)
                if err:
                    return False, f"创建表 {table} 失败: {err}"
        mig_ok, mig_err = _migrate_patient_columns()
        if not mig_ok:
            return False, mig_err
        return True, None
    except Exception as e:
        return False, str(e)


def _migrate_patient_columns() -> Tuple[bool, Optional[str]]:
    """为已有患者表补齐新列与按用户唯一键（SQLite / MySQL 兼容）。"""
    from backend.clinical_owner import migrate_owner_column, migrate_patient_owner_unique_key

    ok, err = migrate_owner_column(TABLE_PATIENTS)
    if not ok:
        return False, err
    ok, err = migrate_patient_owner_unique_key()
    if not ok:
        return False, err
    required = {"disease_duration_years": "FLOAT"}
    existing = mysql_handler.get_table_columns(TABLE_PATIENTS)
    if existing:
        for col, ctype in required.items():
            if col not in existing:
                _, err = mysql_handler.execute(
                    f"ALTER TABLE {TABLE_PATIENTS} ADD COLUMN {col} {ctype}"
                )
                if err and "duplicate" not in str(err).lower():
                    return False, f"迁移患者表失败: {err}"
        return True, None
    return True, None


def _validate_condition_tree(tree: dict) -> Tuple[bool, Optional[str]]:
    """递归校验条件树结构。"""
    if not isinstance(tree, dict):
        return False, "条件树必须是 dict"

    # 叶子条件：{field, op, value}
    if "field" in tree:
        field = tree.get("field", "")
        if field not in ALLOWED_PATIENT_FIELDS:
            return False, f"字段 {field} 不在白名单中: {ALLOWED_PATIENT_FIELDS}"
        leaf_op = tree.get("op", "=").upper()
        if leaf_op not in ALLOWED_OPERATORS:
            return False, f"操作符 {leaf_op} 不在白名单中: {ALLOWED_OPERATORS}"
        return True, None

    if "operator" not in tree:
        return False, "条件树缺少 operator 字段"

    op = tree["operator"].upper()
    if op in ("AND", "OR", "NOT"):
        conditions = tree.get("conditions")
        if not isinstance(conditions, list) or len(conditions) == 0:
            return False, f"逻辑操作符 {op} 的 conditions 必须是非空数组"
        for cond in conditions:
            ok, err = _validate_condition_tree(cond)
            if not ok:
                return False, err
        return True, None

    return False, f"未知逻辑操作符: {op}"


def _build_where_clause(tree: dict, params: list) -> str:
    """递归构建 SQL WHERE 子句和参数列表。"""
    if "field" in tree:
        field = tree["field"]
        leaf_op = tree["op"].upper()
        value = tree.get("value")
        if leaf_op == "IN":
            if not isinstance(value, list) or len(value) == 0:
                return "1=0"
            placeholders = ", ".join(["%s"] * len(value))
            params.extend(value)
            return f"{field} IN ({placeholders})"
        if leaf_op == "NOT IN":
            if not isinstance(value, list) or len(value) == 0:
                return "1=1"
            placeholders = ", ".join(["%s"] * len(value))
            params.extend(value)
            return f"{field} NOT IN ({placeholders})"
        if leaf_op == "BETWEEN":
            if not isinstance(value, list) or len(value) != 2:
                return "1=1"
            params.extend(value)
            return f"{field} BETWEEN %s AND %s"
        if leaf_op == "LIKE":
            params.append(value)
            return f"{field} LIKE %s"
        params.append(value)
        return f"{field} {leaf_op} %s"

    op = tree["operator"].upper()

    if op in ("AND", "OR", "NOT"):
        sub_clauses = []
        for cond in tree.get("conditions", []):
            sub = _build_where_clause(cond, params)
            if sub:
                sub_clauses.append(sub)
        if not sub_clauses:
            return ""
        if op == "NOT":
            return f"NOT ({' AND '.join(sub_clauses)})"
        joiner = f" {op} "
        return f"({joiner.join(sub_clauses)})"

    return "1=1"


def build_sql_from_condition_tree(tree: dict) -> Tuple[str, list]:
    """将条件树 JSON 转为参数化 SQL 和参数列表。"""
    ok, err = _validate_condition_tree(tree)
    if not ok:
        raise ValueError(f"条件树校验失败: {err}")
    params = []
    where = _build_where_clause(tree, params)
    return where, params


def query_patients(
    tree: dict,
    page: int = 1,
    page_size: int = 20,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """查询患者列表（仅当前用户上传的数据）。"""
    ok, err = _ensure_tables()
    if not ok:
        return None, err
    if not owner_user_id or int(owner_user_id) <= 0:
        return {"patients": [], "total": 0, "page": page, "page_size": page_size}, None

    try:
        where, params = build_sql_from_condition_tree(tree)
    except ValueError as e:
        return None, str(e)

    from backend.clinical_owner import owner_scope_sql

    owner_clause, owner_params = owner_scope_sql(int(owner_user_id))
    where = f"({where}) AND {owner_clause}" if where else owner_clause
    params = list(params) + owner_params

    where_clause = f"WHERE {where}" if where else ""

    # 总数
    count_sql = f"SELECT COUNT(*) AS total FROM {TABLE_PATIENTS} {where_clause}"
    count_rows, err = mysql_handler.query(count_sql, tuple(params))
    if err:
        return None, f"查询总数失败: {err}"
    total = count_rows[0]["total"] if count_rows else 0

    # 分页数据
    offset = (page - 1) * page_size
    data_sql = f"SELECT * FROM {TABLE_PATIENTS} {where_clause} ORDER BY id LIMIT %s OFFSET %s"
    data_params = list(params) + [page_size, offset]
    rows, err = mysql_handler.query(data_sql, tuple(data_params))
    if err:
        return None, f"查询数据失败: {err}"

    from backend.clinical_evidence import methodology

    return {
        "patients": list(rows) if rows else [],
        "total": total,
        "page": page,
        "page_size": page_size,
        "methodology": methodology(
            "patient_query",
            caveat="患者列表由参数化 SQL 条件树生成；结果用于可复现队列筛选、纳排和导出，需结合原始病历复核。",
        ),
    }, None


def save_query(
    user_id: int,
    query_name: str,
    condition_tree: dict,
) -> Tuple[Optional[int], Optional[str]]:
    """保存查询条件。"""
    ok, err = _ensure_tables()
    if not ok:
        return None, err

    _, last_id, err = mysql_handler.insert(TABLE_PATIENT_QUERIES, {
        "user_id": user_id,
        "query_name": query_name.strip(),
        "condition_tree": json.dumps(condition_tree, ensure_ascii=False),
    })
    if err:
        return None, f"保存查询条件失败: {err}"
    return last_id, None


def list_saved_queries(user_id: int) -> Tuple[Optional[list], Optional[str]]:
    """列出已保存的查询条件。"""
    ok, err = _ensure_tables()
    if not ok:
        return None, err

    rows, err = mysql_handler.query(
        f"SELECT id, user_id, query_name, created_at FROM {TABLE_PATIENT_QUERIES} WHERE user_id = %s ORDER BY id DESC",
        (user_id,)
    )
    if err:
        return None, f"查询已保存条件失败: {err}"
    return list(rows) if rows else [], None


def get_saved_query(query_id: int, user_id: int) -> Tuple[Optional[dict], Optional[str]]:
    """获取已保存查询（含 condition_tree）。"""
    ok, err = _ensure_tables()
    if not ok:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT id, user_id, query_name, condition_tree, created_at FROM {TABLE_PATIENT_QUERIES} WHERE id = %s AND user_id = %s",
        (query_id, user_id),
    )
    if qerr:
        return None, f"查询失败: {qerr}"
    if not rows:
        return None, "记录不存在"
    row = dict(rows[0])
    ct = row.get("condition_tree")
    if isinstance(ct, str):
        try:
            row["condition_tree"] = json.loads(ct)
        except Exception:
            pass
    return row, None


def import_patients(
    rows: List[dict],
    mode: str = "upsert",
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """从 CSV/Excel 解析后的行批量导入患者库。mode: upsert | append_only"""
    ok, err = _ensure_tables()
    if not ok:
        return None, err
    if not rows:
        return None, "无数据行"
    if not owner_user_id or int(owner_user_id) <= 0:
        return None, "需要登录后才能导入个人临床数据"

    uid = int(owner_user_id)

    inserted, updated, skipped = 0, 0, 0
    errors: List[str] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            skipped += 1
            continue
        pid = str(raw.get("patient_id") or raw.get("id") or "").strip()
        if not pid:
            skipped += 1
            errors.append(f"行{i + 1}: 缺少 patient_id")
            continue
        gender = raw.get("gender")
        if isinstance(gender, str):
            g = gender.strip().upper()
            gender = "female" if g in ("F", "FEMALE", "女") else "male" if g in ("M", "MALE", "男") else gender.lower()
        relapse = raw.get("relapse", 0)
        if isinstance(relapse, str):
            relapse = 1 if relapse.strip().lower() in ("1", "yes", "true", "y") else 0

        payload = {
            "patient_id": pid,
            "owner_user_id": uid,
            "age": raw.get("age"),
            "gender": gender,
            "diagnosis": raw.get("diagnosis"),
            "admission_date": _norm_date_field(raw.get("admission_date")),
            "discharge_date": _norm_date_field(raw.get("discharge_date")),
            "HAMD_total": raw.get("HAMD_total"),
            "HAMA_total": raw.get("HAMA_total"),
            "PHQ9_total": raw.get("PHQ9_total"),
            "disease_duration_years": raw.get("disease_duration_years"),
            "medication": raw.get("medication"),
            "outcome": raw.get("outcome"),
            "relapse": int(relapse) if relapse is not None else 0,
        }
        existing, qerr = mysql_handler.query(
            f"SELECT id FROM {TABLE_PATIENTS} WHERE patient_id = %s AND owner_user_id = %s",
            (pid, uid),
        )
        if qerr:
            errors.append(f"{pid}: 查询失败")
            skipped += 1
            continue
        if existing:
            if mode == "append_only":
                skipped += 1
                continue
            cols = [k for k, v in payload.items() if k != "patient_id" and v is not None]
            if cols:
                set_clause = ", ".join(f"{c} = %s" for c in cols)
                vals = [payload[c] for c in cols] + [pid, uid]
                _, uerr = mysql_handler.execute(
                    f"UPDATE {TABLE_PATIENTS} SET {set_clause} WHERE patient_id = %s AND owner_user_id = %s",
                    tuple(vals),
                )
                if uerr:
                    errors.append(f"{pid}: 更新失败")
                    skipped += 1
                else:
                    updated += 1
        else:
            _, _, ierr = mysql_handler.insert(TABLE_PATIENTS, payload)
            if ierr:
                err_text = str(ierr)
                if "1062" in err_text or "duplicate" in err_text.lower():
                    errors.append(
                        f"{pid}: patient_id 与库内已有记录冲突（请确认已执行 owner 唯一键迁移）"
                    )
                else:
                    errors.append(f"{pid}: 插入失败 ({err_text})")
                skipped += 1
            else:
                inserted += 1

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "total_rows": len(rows),
        "errors": errors[:10],
    }, None


def export_results(
    tree: dict,
    export_format: str = "csv",
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    导出查询结果为 CSV/Excel 文件路径。
    返回临时文件路径字符串（供后续下载）。
    """
    import os
    import tempfile
    import pandas as pd

    if not owner_user_id or int(owner_user_id) <= 0:
        return None, "需要登录后才能导出个人临床数据"

    try:
        where, params = build_sql_from_condition_tree(tree)
    except ValueError as e:
        return None, str(e)

    from backend.clinical_owner import owner_scope_sql

    owner_clause, owner_params = owner_scope_sql(int(owner_user_id))
    where = f"({where}) AND {owner_clause}" if where else owner_clause
    params = list(params) + owner_params

    where_clause = f"WHERE {where}" if where else ""
    sql = f"SELECT * FROM {TABLE_PATIENTS} {where_clause}"
    rows, err = mysql_handler.query(sql, tuple(params))
    if err:
        return None, f"导出查询失败: {err}"

    if not rows:
        return None, "无数据可导出"

    df = pd.DataFrame(rows)
    suffix = ".xlsx" if export_format == "excel" else ".csv"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    if export_format == "excel":
        df.to_excel(tmp_path, index=False, engine="openpyxl")
    else:
        df.to_csv(tmp_path, index=False, encoding="utf-8-sig")

    return tmp_path, None
