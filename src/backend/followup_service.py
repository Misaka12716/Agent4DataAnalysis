# backend/followup_service.py
# 随访数据动态管理与对比分析

import json
from typing import Any, Dict, List, Optional, Tuple
from datetime import date, datetime

from utils.mysql_utils import mysql_handler
from db.followup_schema import TABLE_FOLLOWUPS

TIME_WINDOW_PRESETS = {
    "baseline": (0, 0),
    "week4": (0, 28),
    "week8": (0, 56),
    "week12": (0, 84),
    "custom": None,
}


def resolve_time_window(
    preset: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    anchor_date: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """将预设时间窗转为 (start, end) 日期字符串。"""
    from datetime import timedelta

    preset = (preset or "").strip().lower()
    if preset in ("", "all", "full"):
        if start_date and end_date:
            return start_date, end_date
        return None
    if preset == "custom":
        if start_date and end_date:
            return start_date, end_date
        return None
    if preset not in TIME_WINDOW_PRESETS:
        return (start_date, end_date) if start_date and end_date else None
    days_lo, days_hi = TIME_WINDOW_PRESETS[preset]
    if not anchor_date:
        return None
    try:
        base = datetime.strptime(str(anchor_date)[:10], "%Y-%m-%d")
        lo = base + timedelta(days=days_lo)
        hi = base + timedelta(days=days_hi if days_hi else days_lo)
        return lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")
    except Exception:
        return None


def _ensure_table() -> Tuple[bool, Optional[str]]:
    try:
        if not mysql_handler._check_table_exists(TABLE_FOLLOWUPS):
            from db.followup_schema import FOLLOWUP_TABLE_DDL
            affected, err = mysql_handler.execute(FOLLOWUP_TABLE_DDL)
            if err:
                return False, f"创建随访表失败: {err}"
        from backend.clinical_owner import migrate_owner_column, migrate_followup_owner_unique_key

        ok, err = migrate_owner_column(TABLE_FOLLOWUPS)
        if not ok:
            return False, err
        ok, err = migrate_followup_owner_unique_key()
        if not ok:
            return False, err
        return True, None
    except Exception as e:
        return False, str(e)


def add_followup_record(
    patient_id: str,
    data: dict,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[int], Optional[str]]:
    """添加一条随访记录。返回 record_id。"""
    ok, err = _ensure_table()
    if not ok:
        return None, err

    if not patient_id.strip():
        return None, "patient_id 不能为空"
    if not data.get("visit_date"):
        return None, "visit_date 不能为空"

    insert_data = {
        "patient_id": patient_id.strip(),
        "owner_user_id": int(owner_user_id) if owner_user_id else None,
        "visit_date": data["visit_date"],
        "visit_type": data.get("visit_type"),
        "HAMD_total": data.get("HAMD_total"),
        "HAMA_total": data.get("HAMA_total"),
        "PHQ9_total": data.get("PHQ9_total"),
        "medication": data.get("medication"),
        "medication_dose_mg": data.get("medication_dose_mg"),
        "adverse_events": json.dumps(data.get("adverse_events", []), ensure_ascii=False) if data.get("adverse_events") else None,
        "notes": data.get("notes"),
    }
    _, last_id, err = mysql_handler.insert(TABLE_FOLLOWUPS, insert_data)
    if err:
        return None, f"添加随访记录失败: {err}"
    return last_id, None


def query_followups(
    patient_ids: Optional[List[str]] = None,
    indicators: Optional[List[str]] = None,
    time_range: Optional[Tuple[str, str]] = None,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    查询随访记录。
    return: {patient_id: [records sorted by visit_date]}
    """
    ok, err = _ensure_table()
    if not ok:
        return None, err

    if not owner_user_id or int(owner_user_id) <= 0:
        return {}, None

    from backend.clinical_owner import owner_scope_sql

    owner_clause, owner_params = owner_scope_sql(int(owner_user_id))
    conditions = [owner_clause]
    params = list(owner_params)

    if patient_ids:
        placeholders = ", ".join(["%s"] * len(patient_ids))
        conditions.append(f"patient_id IN ({placeholders})")
        params.extend(patient_ids)

    if time_range and len(time_range) == 2:
        conditions.append("visit_date >= %s AND visit_date <= %s")
        params.extend(time_range)

    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM {TABLE_FOLLOWUPS} WHERE {where} ORDER BY patient_id, visit_date ASC"
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return None, f"查询随访记录失败: {qerr}"

    # Group by patient_id
    result = {}
    for row in rows:
        pid = row["patient_id"]
        if pid not in result:
            result[pid] = []
        # Parse JSON
        if isinstance(row.get("adverse_events"), str):
            try:
                row["adverse_events"] = json.loads(row["adverse_events"])
            except Exception:
                pass
        result[pid].append(row)

    return result, None


def generate_trend_data(
    patient_ids: List[str],
    indicators: List[str],
    time_range: Optional[Tuple[str, str]] = None,
    per_patient: bool = True,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    生成趋势数据 JSON（兼容 echarts/matplotlib）。
    return: {series: {indicator: {time_points: [...], values: [...]}}, patients: [...]}
    """
    grouped, err = query_followups(patient_ids, indicators, time_range, owner_user_id=owner_user_id)
    if err:
        return None, err
    if not grouped:
        return {"series": {}, "patients": []}, None

    # Collect all time points
    all_dates = set()
    patient_data = {}
    for pid, records in grouped.items():
        patient_data[pid] = []
        for r in records:
            d = str(r["visit_date"])
            all_dates.add(d)
            patient_data[pid].append({
                "visit_date": d,
                **{ind: r.get(ind) for ind in indicators if r.get(ind) is not None}
            })

    sorted_dates = sorted(all_dates)

    # Build series per indicator: mean across all patients at each time point
    series = {}
    for ind in indicators:
        series[ind] = {"time_points": sorted_dates, "values": []}
        for d in sorted_dates:
            vals = []
            for pid, records in patient_data.items():
                for r in records:
                    if r["visit_date"] == d and ind in r:
                        vals.append(r[ind])
            series[ind]["values"].append(round(sum(vals) / len(vals), 4) if vals else None)

    # Build chart: per-patient series or cohort mean
    chart_series: Dict[str, Any] = {}
    if per_patient and len(patient_data) <= 8:
        for pid, records in patient_data.items():
            by_date = {r["visit_date"]: r for r in records}
            for ind in indicators:
                key = f"{pid}_{ind}"
                chart_series[key] = [
                    by_date[d].get(ind) if d in by_date else None for d in sorted_dates
                ]
    else:
        for ind in indicators:
            chart_series[ind] = series[ind]["values"]

    from backend.clinical_evidence import methodology

    return {
        "series": series,
        "patients": [
            {"patient_id": pid, "records": records}
            for pid, records in patient_data.items()
        ],
        "chart": {
            "x": sorted_dates,
            "series": chart_series if chart_series else {ind: series[ind]["values"] for ind in indicators if ind in series},
            "mode": "per_patient" if per_patient else "cohort_mean",
        },
        "trend_table": [
            {"visit_date": d, **{ind: series.get(ind, {}).get("values", [None] * len(sorted_dates))[i]
                                  if ind in series and i < len(series[ind].get("values", [])) else None
                                  for ind in indicators}}
            for i, d in enumerate(sorted_dates)
        ],
        "methodology": methodology(
            "followup",
            caveat="随访曲线展示量表指标随时间变化，可用于监测趋势；不能单独证明干预因果效应。",
        ),
    }, None


def compare_groups(
    group_a: List[str],
    group_b: List[str],
    indicators: List[str],
    time_range: Optional[Tuple[str, str]] = None,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    组间对比：返回各时间点两组均值 + 差异检验。
    """
    import numpy as np
    from scipy import stats as scipy_stats

    data_a, err = query_followups(group_a, indicators, time_range, owner_user_id=owner_user_id)
    if err:
        return None, err
    data_b, err = query_followups(group_b, indicators, time_range, owner_user_id=owner_user_id)
    if err:
        return None, err

    # Collect all time points
    all_dates = set()
    for d in [data_a, data_b]:
        if d:
            for records in d.values():
                for r in records:
                    all_dates.add(str(r["visit_date"]))
    sorted_dates = sorted(all_dates)

    comparison = []
    for d in sorted_dates:
        row = {"time_point": d}
        for ind in indicators:
            a_vals = []
            b_vals = []
            if data_a:
                for records in data_a.values():
                    for r in records:
                        if str(r["visit_date"]) == d and r.get(ind) is not None:
                            a_vals.append(r[ind])
            if data_b:
                for records in data_b.values():
                    for r in records:
                        if str(r["visit_date"]) == d and r.get(ind) is not None:
                            b_vals.append(r[ind])

            a_mean = round(np.mean(a_vals), 4) if a_vals else None
            b_mean = round(np.mean(b_vals), 4) if b_vals else None
            p_val = None
            if len(a_vals) >= 3 and len(b_vals) >= 3:
                try:
                    _, p_val = scipy_stats.ttest_ind(a_vals, b_vals, equal_var=False)
                    p_val = round(float(p_val), 4)
                except Exception:
                    pass

            row[ind] = {
                "group_a_mean": a_mean,
                "group_b_mean": b_mean,
                "group_a_n": len(a_vals),
                "group_b_n": len(b_vals),
                "p_value": p_val,
                "significant": p_val < 0.05 if p_val is not None else None,
            }
        comparison.append(row)

    from backend.clinical_evidence import methodology

    return {
        "comparison": comparison,
        "group_a_ids": group_a,
        "group_b_ids": group_b,
        "methodology": methodology(
            "followup",
            ["welch_1947"],
            caveat="组间随访比较使用各时间点描述统计和 Welch t 检验；多重比较和基线不平衡需另行控制。",
        ),
    }, None


def _normalize_date_value(value: Any) -> str:
    """统一随访/入院等日期为 YYYY-MM-DD（兼容 Excel 序列号与 2024/1/15 格式）。"""
    import re
    from datetime import datetime, timedelta

    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            serial = float(value)
            if 20000 <= serial <= 60000:
                base = datetime(1899, 12, 30)
                return (base + timedelta(days=serial)).strftime("%Y-%m-%d")
        except Exception:
            pass
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return ""
        if "T" in v:
            v = v.split("T", 1)[0]
        if " " in v:
            v = v.split(" ", 1)[0]
        m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", v)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
        if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            return v
        return v[:10] if len(v) >= 10 else v
    s = str(value).strip()
    if " " in s:
        s = s.split(" ", 1)[0]
    m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return s[:10] if len(s) >= 10 else s


def import_followups(
    rows: List[dict],
    mode: str = "upsert",
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """批量导入随访记录。必填：patient_id, visit_date。mode: upsert | append_only"""
    ok, err = _ensure_table()
    if not ok:
        return None, err
    if not rows:
        return None, "无数据行"
    if not owner_user_id or int(owner_user_id) <= 0:
        return None, "需要登录后才能导入个人随访数据"

    uid = int(owner_user_id)
    inserted, updated, skipped = 0, 0, 0
    errors: List[str] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            skipped += 1
            continue
        pid = str(raw.get("patient_id") or "").strip()
        vdate = _normalize_date_value(raw.get("visit_date"))
        if not pid or not vdate:
            skipped += 1
            errors.append(f"行{i + 1}: 缺少 patient_id 或 visit_date")
            continue
        payload = {
            "visit_type": raw.get("visit_type"),
            "HAMD_total": raw.get("HAMD_total"),
            "HAMA_total": raw.get("HAMA_total"),
            "PHQ9_total": raw.get("PHQ9_total"),
            "medication": raw.get("medication"),
            "medication_dose_mg": raw.get("medication_dose_mg"),
            "notes": raw.get("notes"),
        }
        existing, qerr = mysql_handler.query(
            f"SELECT id FROM {TABLE_FOLLOWUPS} WHERE patient_id = %s AND visit_date = %s AND owner_user_id = %s",
            (pid, vdate, uid),
        )
        if qerr:
            errors.append(f"{pid}/{vdate}: 查询失败")
            skipped += 1
            continue
        if existing:
            if mode == "append_only":
                skipped += 1
                continue
            cols = [k for k, v in payload.items() if v is not None]
            if cols:
                set_clause = ", ".join(f"{c} = %s" for c in cols)
                vals = [payload[c] for c in cols] + [pid, vdate, uid]
                _, uerr = mysql_handler.execute(
                    f"UPDATE {TABLE_FOLLOWUPS} SET {set_clause} WHERE patient_id = %s AND visit_date = %s AND owner_user_id = %s",
                    tuple(vals),
                )
                if uerr:
                    errors.append(f"{pid}/{vdate}: 更新失败")
                    skipped += 1
                else:
                    updated += 1
            else:
                skipped += 1
        else:
            rid, ierr = add_followup_record(pid, {"visit_date": vdate, **payload}, owner_user_id=uid)
            if ierr:
                err_text = str(ierr)
                if "1062" in err_text or "duplicate" in err_text.lower():
                    errors.append(
                        f"{pid}/{vdate}: 随访记录与库内冲突（请确认已执行 owner 唯一键迁移）"
                    )
                else:
                    errors.append(f"{pid}/{vdate}: {err_text}")
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
