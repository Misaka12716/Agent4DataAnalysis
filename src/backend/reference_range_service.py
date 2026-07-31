# backend/reference_range_service.py
# 参考区间管理 + 异常评估 + 批量评估 + 横向对比

import json
from typing import Any, Dict, List, Optional, Tuple

from utils.mysql_utils import mysql_handler
from db.reference_schema import TABLE_REFERENCE_RANGES


def _ensure_table() -> Tuple[bool, Optional[str]]:
    try:
        if not mysql_handler._check_table_exists(TABLE_REFERENCE_RANGES):
            from db.reference_schema import REFERENCE_RANGE_TABLE_DDL
            affected, err = mysql_handler.execute(REFERENCE_RANGE_TABLE_DDL)
            if err:
                return False, f"创建参考区间表失败: {err}"
        from backend.clinical_owner import migrate_owner_column

        ok, err = migrate_owner_column(TABLE_REFERENCE_RANGES)
        if not ok:
            return False, err
        return True, None
    except Exception as e:
        return False, str(e)


def manage_reference_range(action: str, data: dict) -> Tuple[Optional[Any], Optional[str]]:
    """
    CRUD 操作统一入口。
    action: create | list | get | update | delete
    """
    ok, err = _ensure_table()
    if not ok:
        return None, err

    if action == "create":
        required = ["indicator", "lower_bound", "upper_bound"]
        for f in required:
            if f not in data or data[f] is None:
                return None, f"{f} 不能为空"
        insert_data = {
            "indicator": data["indicator"],
            "owner_user_id": data.get("owner_user_id"),
            "gender": data.get("gender"),
            "age_range_lower": data.get("age_range_lower"),
            "age_range_upper": data.get("age_range_upper"),
            "diagnosis": data.get("diagnosis"),
            "lower_bound": data["lower_bound"],
            "upper_bound": data["upper_bound"],
            "unit": data.get("unit"),
            "source": data.get("source"),
        }
        _, last_id, err = mysql_handler.insert(TABLE_REFERENCE_RANGES, insert_data)
        if err:
            return None, f"创建参考区间失败: {err}"
        rows, qerr = mysql_handler.query(f"SELECT * FROM {TABLE_REFERENCE_RANGES} WHERE id = %s", (last_id,))
        if qerr or not rows:
            return None, f"查询新记录失败: {qerr}"
        return rows[0], None

    elif action == "list":
        owner_id = data.get("owner_user_id")
        if owner_id:
            sql = f"SELECT * FROM {TABLE_REFERENCE_RANGES} WHERE owner_user_id = %s ORDER BY id"
            rows, qerr = mysql_handler.query(sql, (int(owner_id),))
        else:
            sql = f"SELECT * FROM {TABLE_REFERENCE_RANGES} ORDER BY id"
            rows, qerr = mysql_handler.query(sql)
        if qerr:
            return None, f"查询列表失败: {qerr}"
        return list(rows) if rows else [], None

    elif action == "get":
        rid = data.get("id")
        if not rid:
            return None, "id 不能为空"
        owner_id = data.get("owner_user_id")
        if owner_id:
            rows, qerr = mysql_handler.query(
                f"SELECT * FROM {TABLE_REFERENCE_RANGES} WHERE id = %s AND owner_user_id = %s",
                (rid, int(owner_id)),
            )
        else:
            rows, qerr = mysql_handler.query(f"SELECT * FROM {TABLE_REFERENCE_RANGES} WHERE id = %s", (rid,))
        if qerr:
            return None, f"查询失败: {qerr}"
        if not rows:
            return None, "记录不存在"
        return rows[0], None

    elif action == "update":
        rid = data.get("id")
        if not rid:
            return None, "id 不能为空"
        owner_id = data.get("owner_user_id")
        # 简单更新：允许更新 lower_bound, upper_bound, unit, source
        updates = []
        params = []
        for f in ["lower_bound", "upper_bound", "unit", "source", "gender", "age_range_lower", "age_range_upper", "diagnosis"]:
            if f in data and data[f] is not None:
                updates.append(f"{f} = %s")
                params.append(data[f])
        if not updates:
            return None, "无更新字段"
        params.append(rid)
        if owner_id:
            params.append(int(owner_id))
            sql = f"UPDATE {TABLE_REFERENCE_RANGES} SET {', '.join(updates)} WHERE id = %s AND owner_user_id = %s"
        else:
            sql = f"UPDATE {TABLE_REFERENCE_RANGES} SET {', '.join(updates)} WHERE id = %s"
        _, err = mysql_handler.execute(sql, tuple(params))
        if err:
            return None, f"更新失败: {err}"
        if owner_id:
            rows, qerr = mysql_handler.query(
                f"SELECT * FROM {TABLE_REFERENCE_RANGES} WHERE id = %s AND owner_user_id = %s",
                (rid, int(owner_id)),
            )
        else:
            rows, qerr = mysql_handler.query(f"SELECT * FROM {TABLE_REFERENCE_RANGES} WHERE id = %s", (rid,))
        if qerr or not rows:
            return None, f"查询更新后记录失败: {qerr}"
        return rows[0], None

    elif action == "delete":
        rid = data.get("id")
        if not rid:
            return None, "id 不能为空"
        owner_id = data.get("owner_user_id")
        if owner_id:
            _, err = mysql_handler.execute(
                f"DELETE FROM {TABLE_REFERENCE_RANGES} WHERE id = %s AND owner_user_id = %s",
                (rid, int(owner_id)),
            )
        else:
            _, err = mysql_handler.execute(f"DELETE FROM {TABLE_REFERENCE_RANGES} WHERE id = %s", (rid,))
        if err:
            return None, f"删除失败: {err}"
        return {"deleted": True}, None

    else:
        return None, f"未知操作: {action}"


def _match_reference_range(
    indicator: str,
    gender: Optional[str] = None,
    age: Optional[int] = None,
    diagnosis: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """查找最匹配的参考区间。"""
    ok, err = _ensure_table()
    if not ok:
        return None, err

    owner_filter = " AND owner_user_id = %s" if owner_user_id else ""
    owner_param = (int(owner_user_id),) if owner_user_id else ()

    # 精确匹配
    if diagnosis:
        sql = f"""SELECT * FROM {TABLE_REFERENCE_RANGES}
            WHERE indicator = %s
              AND (gender = %s OR gender IS NULL)
              AND (age_range_lower <= %s OR age_range_lower IS NULL)
              AND (age_range_upper >= %s OR age_range_upper IS NULL)
              AND (diagnosis = %s OR diagnosis IS NULL)
              {owner_filter}
            ORDER BY
              CASE WHEN diagnosis = %s THEN 0 WHEN diagnosis IS NOT NULL THEN 1 ELSE 2 END,
              CASE WHEN gender IS NOT NULL THEN 0 ELSE 1 END
            LIMIT 1"""
        rows, qerr = mysql_handler.query(
            sql,
            (indicator, gender, age, age, diagnosis, diagnosis) + owner_param,
        )
    else:
        sql = f"""SELECT * FROM {TABLE_REFERENCE_RANGES}
            WHERE indicator = %s
              AND (gender = %s OR gender IS NULL)
              AND (age_range_lower <= %s OR age_range_lower IS NULL)
              AND (age_range_upper >= %s OR age_range_upper IS NULL)
              {owner_filter}
            ORDER BY
              CASE WHEN gender IS NOT NULL THEN 0 ELSE 1 END,
              CASE WHEN age_range_lower IS NOT NULL THEN 0 ELSE 1 END
            LIMIT 1"""
        rows, qerr = mysql_handler.query(sql, (indicator, gender, age, age) + owner_param)
    if qerr:
        return None, qerr
    if rows:
        return rows[0], None

    # 无分层匹配，尝试仅指标匹配
    sql = f"SELECT * FROM {TABLE_REFERENCE_RANGES} WHERE indicator = %s{owner_filter} LIMIT 1"
    rows, qerr = mysql_handler.query(sql, (indicator,) + owner_param)
    if qerr:
        return None, qerr
    return rows[0] if rows else None, None


def evaluate_abnormality(
    patient_indicators: Dict[str, float],
    gender: Optional[str] = None,
    age: Optional[int] = None,
    diagnosis: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[list], Optional[str]]:
    """
    评估单个患者的指标异常。
    patient_indicators: {indicator: value} 如 {"HAMD_total": 22}
    return: [{indicator, value, lower, upper, is_abnormal, deviation_pct}]
    """
    from backend.clinical_evidence import get_evidence, methodology

    evidence_by_indicator = {
        "HAMD_total": ["hamd_1960", "zimmerman_2013", "richardson_2010_adolescent_phq9"],
        "HAMA_total": ["hama_1959", "matza_2010"],
        "PHQ9_total": ["phq9_2001", "richardson_2010_adolescent_phq9"],
        "GAD7_total": ["gad7_2006"],
        "PANSS_total": ["panss_1987", "leucht_2005_panss"],
        "BPRS_total": ["leucht_2005_bprs"],
        "PSQI_total": ["psqi_1989"],
        "ISI_total": ["isi_2011"],
    }
    results = []
    for indicator, value in patient_indicators.items():
        ref, err = _match_reference_range(indicator, gender, age, diagnosis, owner_user_id=owner_user_id)
        if err:
            return None, err
        if not ref:
            results.append({
                "indicator": indicator,
                "value": value,
                "lower": None,
                "upper": None,
                "is_abnormal": None,
                "deviation_pct": None,
                "note": "无匹配参考区间",
                "evidence": get_evidence(evidence_by_indicator.get(indicator, [])),
                "interpretation_note": "未匹配到本地参考区间，不能据此判定异常。",
            })
            continue

        lower = ref["lower_bound"]
        upper = ref["upper_bound"]
        is_abnormal = value < lower or value > upper

        if value > upper:
            deviation_pct = round((value - upper) / upper * 100, 1) if upper else None
        elif value < lower:
            deviation_pct = round((value - lower) / lower * 100, 1) if lower else None
        else:
            deviation_pct = 0.0

        results.append({
            "indicator": indicator,
            "value": value,
            "lower": lower,
            "upper": upper,
            "is_abnormal": is_abnormal,
            "deviation_pct": deviation_pct,
            "unit": ref.get("unit"),
            "source": ref.get("source"),
            "evidence": get_evidence(evidence_by_indicator.get(indicator, [])),
            "interpretation_note": "参考区间/阈值用于异常提示和横向比较，不替代结构化临床诊断。",
            "methodology": methodology("reference_range", evidence_by_indicator.get(indicator, [])),
        })

    return results, None


def batch_evaluate(
    patient_ids: List[str],
    indicators: List[str],
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    批量评估：对一组患者计算异常率统计。
    return: {abnormal_rate: {indicator: rate}, details: [{patient_id, results}]}
    """
    ok, err = _ensure_table()
    if not ok:
        return None, err

    # 从患者表批量获取数据
    from db.patient_schema import TABLE_PATIENTS
    cols = ["patient_id", "age", "gender", "diagnosis"] + indicators
    cols_str = ", ".join(cols)
    if not patient_ids:
        if not owner_user_id:
            return {"abnormal_rates": {}, "details": [], "total_patients": 0, "evaluated_patients": 0}, None
        sql = f"SELECT {cols_str} FROM {TABLE_PATIENTS} WHERE owner_user_id = %s ORDER BY patient_id LIMIT 500"
        rows, qerr = mysql_handler.query(sql, (int(owner_user_id),))
    else:
        placeholders = ", ".join(["%s"] * len(patient_ids))
        if owner_user_id:
            sql = f"SELECT {cols_str} FROM {TABLE_PATIENTS} WHERE patient_id IN ({placeholders}) AND owner_user_id = %s"
            rows, qerr = mysql_handler.query(sql, tuple(patient_ids) + (int(owner_user_id),))
        else:
            sql = f"SELECT {cols_str} FROM {TABLE_PATIENTS} WHERE patient_id IN ({placeholders})"
            rows, qerr = mysql_handler.query(sql, tuple(patient_ids))
    if qerr:
        return None, f"批量查询患者数据失败: {qerr}"

    details = []
    abnormal_counts = {ind: 0 for ind in indicators}
    total_evaluated = {ind: 0 for ind in indicators}

    for row in rows:
        patient_indicators = {ind: row.get(ind) for ind in indicators if row.get(ind) is not None}
        if not patient_indicators:
            continue

        results, err = evaluate_abnormality(
            patient_indicators,
            gender=row.get("gender"),
            age=row.get("age"),
            diagnosis=row.get("diagnosis"),
            owner_user_id=owner_user_id,
        )
        if err:
            continue

        details.append({
            "patient_id": row["patient_id"],
            "results": results,
        })

        for r in results:
            if r.get("is_abnormal") is True:
                abnormal_counts[r["indicator"]] = abnormal_counts.get(r["indicator"], 0) + 1
            if r.get("is_abnormal") is not None:
                total_evaluated[r["indicator"]] = total_evaluated.get(r["indicator"], 0) + 1

    abnormal_rates = {}
    for ind in indicators:
        total = total_evaluated[ind]
        abnormal_rates[ind] = round(abnormal_counts[ind] / total * 100, 1) if total > 0 else None

    from backend.clinical_evidence import methodology

    return {
        "abnormal_rates": abnormal_rates,
        "details": details,
        "total_patients": len(patient_ids) if patient_ids else len(rows),
        "evaluated_patients": len(rows),
        "interpretation_note": (
            "异常率 = 量表得分超出已配置参考区间上/下限的患者比例（0–100%）。"
            "精神科队列患者得分通常高于「无抑郁/无焦虑」人群参考上限，故异常率偏高属常见现象，"
            "表示症状严重程度超出参考阈值，而非新发病例检出率。"
            "请确认已上传参考区间；未匹配参考区间的记录不计入分母。"
        ),
        "methodology": methodology(
            "reference_range",
            caveat="批量异常率是基于已配置参考区间的描述性质量/异常提示指标。",
        ),
    }, None


def compare_to_reference_population(
    cohort_patient_ids: List[str],
    reference_population_ids: List[str],
    indicators: List[str],
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    横向对比：将研究队列与参考人群进行比较。
    返回各指标的两组均值、差异检验结果。
    """
    from db.patient_schema import TABLE_PATIENTS
    import numpy as np
    from scipy import stats as scipy_stats

    def _get_group_data(ids):
        if not ids:
            return None
        placeholders = ", ".join(["%s"] * len(ids))
        cols = ["patient_id"] + indicators
        params = list(ids)
        owner_clause = ""
        if owner_user_id:
            owner_clause = " AND owner_user_id = %s"
            params.append(int(owner_user_id))
        sql = f"SELECT {', '.join(cols)} FROM {TABLE_PATIENTS} WHERE patient_id IN ({placeholders}){owner_clause}"
        rows, err = mysql_handler.query(sql, tuple(params))
        if err:
            return None
        return rows

    cohort = _get_group_data(cohort_patient_ids)
    ref_pop = _get_group_data(reference_population_ids)

    if not cohort:
        return None, "无法获取研究队列数据，请先在「患者纳排」建立队列"
    if not ref_pop and owner_user_id and reference_population_ids == []:
        from backend.clinical_data_service import list_patient_ids

        all_ids = list_patient_ids(int(owner_user_id), limit=500)
        ref_ids = [pid for pid in all_ids if pid not in set(cohort_patient_ids)]
        ref_pop = _get_group_data(ref_ids[:200]) if ref_ids else None

    if not ref_pop:
        return None, "无法获取对比组数据，请填写对比组 B 患者 ID"

    comparison = []
    for ind in indicators:
        cohort_vals = [r[ind] for r in cohort if r.get(ind) is not None]
        ref_vals = [r[ind] for r in ref_pop if r.get(ind) is not None]
        if len(cohort_vals) < 3 or len(ref_vals) < 3:
            comparison.append({
                "indicator": ind,
                "cohort_mean": np.mean(cohort_vals) if cohort_vals else None,
                "reference_mean": np.mean(ref_vals) if ref_vals else None,
                "cohort_n": len(cohort_vals),
                "reference_n": len(ref_vals),
                "test": "insufficient data",
                "p_value": None,
                "significant": None,
            })
            continue
        try:
            t_stat, p_val = scipy_stats.ttest_ind(cohort_vals, ref_vals, equal_var=False)
            comparison.append({
                "indicator": ind,
                "cohort_mean": round(np.mean(cohort_vals), 4),
                "reference_mean": round(np.mean(ref_vals), 4),
                "cohort_n": len(cohort_vals),
                "reference_n": len(ref_vals),
                "test": "Welch t-test",
                "t_statistic": round(float(t_stat), 4),
                "p_value": round(float(p_val), 4),
                "significant": bool(p_val < 0.05),
            })
        except Exception as e:
            comparison.append({
                "indicator": ind,
                "error": str(e),
            })

    from backend.clinical_evidence import methodology

    return {
        "comparison": comparison,
        "methodology": methodology(
            "reference_range",
            ["welch_1947"],
            caveat="人群横向对比使用 Welch t 检验；结果为组间统计差异提示，不代表临床显著性或因果关系。",
        ),
    }, None


def import_reference_ranges(
    rows: List[dict],
    mode: str = "upsert",
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """批量导入参考区间。必填：indicator, lower_bound, upper_bound。"""
    ok, err = _ensure_table()
    if not ok:
        return None, err
    if not rows:
        return None, "无数据行"
    if not owner_user_id or int(owner_user_id) <= 0:
        return None, "需要登录后才能导入个人参考区间"

    uid = int(owner_user_id)
    inserted, updated, skipped = 0, 0, 0
    errors: List[str] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            skipped += 1
            continue
        indicator = str(raw.get("indicator") or "").strip()
        if not indicator:
            skipped += 1
            errors.append(f"行{i + 1}: 缺少 indicator")
            continue
        try:
            lo = float(raw["lower_bound"])
            hi = float(raw["upper_bound"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            errors.append(f"行{i + 1}: lower_bound/upper_bound 无效")
            continue
        gender = raw.get("gender")
        if isinstance(gender, str):
            g = gender.strip().upper()
            gender = "female" if g in ("F", "FEMALE", "女") else "male" if g in ("M", "MALE", "男") else gender.lower() or None
        payload = {
            "indicator": indicator,
            "gender": gender,
            "age_range_lower": raw.get("age_range_lower"),
            "age_range_upper": raw.get("age_range_upper"),
            "diagnosis": raw.get("diagnosis"),
            "lower_bound": lo,
            "upper_bound": hi,
            "unit": raw.get("unit"),
            "source": raw.get("source"),
        }
        row, cerr = manage_reference_range("create", {**payload, "owner_user_id": uid})
        if cerr:
            if mode == "append_only" and "失败" in str(cerr):
                skipped += 1
            else:
                errors.append(f"{indicator}: {cerr}")
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
