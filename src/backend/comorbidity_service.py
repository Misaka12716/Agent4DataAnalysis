# backend/comorbidity_service.py
# 精神疾病谱系/共病聚焦分析 — 矩阵计算 + 谱系关系 + 聚类 + 可视化数据

import json
import math
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    return value

from utils.mysql_utils import mysql_handler
from db.comorbidity_schema import TABLE_COMORBIDITY


def _query_cohort_rows(
    cohort_ids: List[str],
    select_cols: str,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[List[dict]], Optional[str]]:
    from db.patient_schema import TABLE_PATIENTS

    if not cohort_ids:
        return None, "cohort_ids 不能为空"
    placeholders = ", ".join(["%s"] * len(cohort_ids))
    params: List[Any] = list(cohort_ids)
    owner_clause = ""
    if owner_user_id:
        owner_clause = " AND owner_user_id = %s"
        params.append(int(owner_user_id))
    sql = f"SELECT {select_cols} FROM {TABLE_PATIENTS} WHERE patient_id IN ({placeholders}){owner_clause}"
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return None, f"查询患者数据失败: {qerr}"
    return list(rows or []), None


DIAGNOSIS_PAIRS = [
    ("depression", "anxiety"),
    ("depression", "sleep_disorder"),
    ("depression", "schizophrenia"),
    ("anxiety", "sleep_disorder"),
    ("schizophrenia", "anxiety"),
    ("sleep_disorder", "anxiety"),
    ("depression", "child_adolescent"),
    ("anxiety", "child_adolescent"),
]


def _ensure_table() -> Tuple[bool, Optional[str]]:
    try:
        from backend.clinical_owner import migrate_owner_column

        if not mysql_handler._check_table_exists(TABLE_COMORBIDITY):
            from db.comorbidity_schema import COMORBIDITY_TABLE_DDL
            affected, err = mysql_handler.execute(COMORBIDITY_TABLE_DDL)
            if err:
                return False, f"创建共病分析表失败: {err}"
        ok, err = migrate_owner_column(TABLE_COMORBIDITY)
        if not ok:
            return False, err
        return True, None
    except Exception as e:
        return False, str(e)


# 量表阈值均取自经验验证的"中度及以上"severity 分级下限，而非任意拍板数值：
#   HAMA >= 15  — Matza LS et al (2010, DOI 10.1002/mpr.323) 中度焦虑起点
#   HAMD >= 17  — Zimmerman M et al (2013, PMID 23759278) 中度抑郁起点
#   PHQ9 >= 15  — Kroenke K et al (2001) 重度抑郁起点；作为伴发睡眠紊乱信号的弱代理指标
#                 （PHQ-9 仅含 1 个睡眠条目，非专用失眠量表，可用时应优先使用 PSQI/ISI 总分）
HAMA_MODERATE_CUTOFF = 15
HAMD_MODERATE_CUTOFF = 17
PHQ9_SEVERE_CUTOFF = 15


def _diagnosis_set(row: dict) -> set:
    diags = set()
    primary = (row.get("diagnosis") or "").strip()
    if primary:
        diags.add(primary)
    hamd = float(row.get("HAMD_total") or 0)
    hama = float(row.get("HAMA_total") or 0)
    phq9 = float(row.get("PHQ9_total") or 0)
    if primary == "depression" and hama >= HAMA_MODERATE_CUTOFF:
        diags.add("anxiety")
    if primary == "anxiety" and hamd >= HAMD_MODERATE_CUTOFF:
        diags.add("depression")
    if primary in ("depression", "anxiety") and phq9 >= PHQ9_SEVERE_CUTOFF:
        diags.add("sleep_disorder")
    if primary == "sleep_disorder" and hama >= HAMA_MODERATE_CUTOFF:
        diags.add("anxiety")
    return diags


def compute_matrix_from_rows(rows: List[Dict[str, Any]]) -> dict:
    """纯函数：基于 [{diagnosis, HAMD_total, HAMA_total, PHQ9_total}, ...] 计算共病矩阵。

    不依赖数据库，供 DB 队列（compute_comorbidity_matrix）与模板算子管线
    （template_step_executor 对任意 DataFrame）共用同一套推断口径。
    """
    from backend.clinical_evidence import methodology

    patient_diags = [_diagnosis_set(r) for r in rows]
    diag_counts = Counter()
    for ds in patient_diags:
        for d in ds:
            diag_counts[d] += 1

    all_diagnoses = sorted(diag_counts.keys())
    total = len(rows)

    frequency_matrix = {}
    cooccurrence_rate = {}
    for d1 in all_diagnoses:
        frequency_matrix[d1] = {}
        cooccurrence_rate[d1] = {}
        for d2 in all_diagnoses:
            if d1 == d2:
                frequency_matrix[d1][d2] = diag_counts[d1]
                cooccurrence_rate[d1][d2] = round(diag_counts[d1] / total, 4) if total > 0 else 0
            else:
                co_count = sum(1 for ds in patient_diags if d1 in ds and d2 in ds)
                frequency_matrix[d1][d2] = co_count
                cooccurrence_rate[d1][d2] = round(co_count / total, 4) if total > 0 else 0

    return {
        "diagnoses": all_diagnoses,
        "frequency_matrix": frequency_matrix,
        "matrix": frequency_matrix,
        "cooccurrence_rate": cooccurrence_rate,
        "total_patients": total,
        "diagnosis_counts": dict(diag_counts),
        "inference_note": (
            "共病集含主诊断 + 量表阈值推断的伴发症状信号：HAMA>=15（中度焦虑，Matza 2010）、"
            "HAMD>=17（中度抑郁，Zimmerman 2013）、PHQ9>=15（重度抑郁，作为睡眠紊乱弱代理信号，Kroenke 2001）。"
            "阈值取自经验验证的严重度分级下限，但推断结果不是正式共病诊断。"
        ),
        "methodology": methodology(
            "comorbidity",
            ["zimmerman_2013", "matza_2010", "phq9_2001"],
            caveat="共病矩阵展示诊断/症状信号的共现结构，不替代结构化诊断访谈或病历复核。",
        ),
    }


def compute_comorbidity_matrix(
    cohort_ids: List[str],
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    计算共病频率矩阵和共现率矩阵。
    假设患者表中有 diagnosis 和 comorbid_diagnoses 字段，
    或直接基于 diagnosis 字段计算共现。
    """
    rows, err = _query_cohort_rows(
        cohort_ids,
        "patient_id, diagnosis, HAMD_total, HAMA_total, PHQ9_total",
        owner_user_id=owner_user_id,
    )
    if err:
        return None, err

    if not rows:
        return None, "无患者数据"

    return compute_matrix_from_rows(rows), None


def infer_primary_diagnosis(
    cohort_ids: Optional[List[str]],
    owner_user_id: Optional[int] = None,
) -> Optional[str]:
    """从队列主诊断字段众数推断谱系分析锚点。"""
    if not cohort_ids:
        return None
    rows, err = _query_cohort_rows(cohort_ids, "diagnosis", owner_user_id=owner_user_id)
    if err or not rows:
        return None
    counts = Counter((r.get("diagnosis") or "").strip() for r in rows if r.get("diagnosis"))
    counts = Counter({k: v for k, v in counts.items() if k})
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def analyze_spectrum_relationship(
    primary_diagnosis: str,
    cohort_ids: Optional[List[str]] = None,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    分析特定诊断的谱系关系：基于 _diagnosis_set 推断集，计算与其他诊断/症状信号的 RR、OR 和 p 值。
    """
    import numpy as np
    from scipy import stats as scipy_stats

    primary = (primary_diagnosis or "").strip()
    if not primary:
        return None, "primary_diagnosis 不能为空"

    if not cohort_ids:
        return None, "cohort_ids 不能为空"

    rows, err = _query_cohort_rows(
        cohort_ids,
        "patient_id, diagnosis, HAMD_total, HAMA_total, PHQ9_total",
        owner_user_id=owner_user_id,
    )
    if err:
        return None, err
    if not rows:
        return None, "无患者数据"

    patient_diags = [_diagnosis_set(r) for r in rows]
    total = len(patient_diags)
    has_primary = [primary in ds for ds in patient_diags]
    primary_count = sum(has_primary)
    if primary_count == 0:
        return None, (
            f"队列中无 primary_diagnosis={primary} 的患者（含量表推断后仍无该诊断信号）。"
            "请更换主诊断或扩大患者范围。"
        )

    other_diags = sorted({d for ds in patient_diags for d in ds if d != primary})
    relationships = []
    for other in other_diags:
        has_other = [other in ds for ds in patient_diags]
        a = sum(1 for i in range(total) if has_primary[i] and has_other[i])
        b = sum(1 for i in range(total) if has_primary[i] and not has_other[i])
        c = sum(1 for i in range(total) if not has_primary[i] and has_other[i])
        d_val = sum(1 for i in range(total) if not has_primary[i] and not has_other[i])
        try:
            table = np.array([[a, b], [c, d_val]])
            odds_ratio, p_val = scipy_stats.fisher_exact(table)
            rr = None
            if (a + b) > 0 and (c + d_val) > 0 and (c + d_val) > 0:
                rr = round((a / (a + b)) / (c / (c + d_val)), 4)
            relationships.append({
                "comorbid_diagnosis": other,
                "n_with_primary": primary_count,
                "n_with_both": a,
                "n_with_comorbid_only": c,
                "cohort_total": total,
                "relative_risk": rr,
                "odds_ratio": round(float(odds_ratio), 4),
                "p_value": round(float(p_val), 4),
                "significant": bool(p_val < 0.05),
            })
        except Exception:
            relationships.append({
                "comorbid_diagnosis": other,
                "n_with_primary": primary_count,
                "n_with_both": a,
                "error": "计算失败",
            })

    sig_count = sum(1 for r in relationships if r.get("significant"))
    from backend.clinical_evidence import methodology

    payload = {
        "primary_diagnosis": primary,
        "relationships": relationships,
        "pairs": relationships,
        "total_patients": total,
        "summary": {
            "primary_n": primary_count,
            "comorbid_types": len(other_diags),
            "significant_pairs": sig_count,
        },
        "inference_note": (
            "谱系分析基于主诊断字段 + 量表阈值推断的伴发症状信号（与共病矩阵同一口径）。"
            "RR/OR 为队列内探索性关联，不能替代正式共病诊断。"
        ),
        "methodology": methodology(
            "comorbidity",
            ["mood_anxiety_comorbidity_2020"],
            caveat="谱系关系基于当前队列频数和 Fisher 精确检验，结果为探索性关联。",
        ),
    }
    return payload, None


def cluster_comorbidity_patterns(
    cohort_ids: List[str],
    n_clusters: int = 3,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    聚类共病模式：基于诊断分布进行简单聚类。
    """
    import numpy as np

    rows, err = _query_cohort_rows(
        cohort_ids,
        "patient_id, diagnosis, age, HAMD_total, HAMA_total, PHQ9_total",
        owner_user_id=owner_user_id,
    )
    if err:
        return None, err

    if len(rows) < 10:
        return None, "患者数不足，无法聚类（至少需要 10 条）"

    diag_set = set()
    for r in rows:
        diag_set.update(_diagnosis_set(r))
    diag_set = sorted(d for d in diag_set if d)
    if len(diag_set) < 2:
        return None, "诊断种类不足，无法聚类（请扩大患者范围至 P001–P020，需含至少 2 种诊断）"

    # 简单聚类：按 diagnosis + HAMD_total 分层
    clusters = []
    for i in range(n_clusters):
        clusters.append({"cluster_id": i, "patients": [], "diagnosis_distribution": {}, "mean_hamd": 0})

    # 按 HAMD 排序后均分
    sorted_rows = sorted(rows, key=lambda r: r.get("HAMD_total", 0) or 0)
    chunk_size = max(1, len(sorted_rows) // n_clusters)
    for i, row in enumerate(sorted_rows):
        cluster_idx = min(i // chunk_size, n_clusters - 1)
        clusters[cluster_idx]["patients"].append(row["patient_id"])
        diag = row.get("diagnosis", "unknown")
        clusters[cluster_idx]["diagnosis_distribution"][diag] = clusters[cluster_idx]["diagnosis_distribution"].get(diag, 0) + 1

    for c in clusters:
        if c["patients"]:
            hamd_vals = []
            for r in rows:
                if r["patient_id"] in c["patients"] and r.get("HAMD_total") is not None:
                    hamd_vals.append(r["HAMD_total"])
            c["mean_hamd"] = round(np.mean(hamd_vals), 2) if hamd_vals else 0
            c["count"] = len(c["patients"])

    from backend.clinical_evidence import methodology

    return {
        "n_clusters": n_clusters,
        "clusters": clusters,
        "total_patients": len(rows),
        "methodology": methodology(
            "comorbidity",
            caveat="聚类用于探索共病/严重度模式，聚类编号不是医学诊断类别。",
        ),
    }, None


def generate_heatmap_data(
    matrix: Dict[str, Dict[str, float]],
    labels: List[str],
) -> Tuple[Optional[dict], Optional[str]]:
    """
    生成热图 JSON 数据（兼容 echarts/plotly）。
    matrix: {row_label: {col_label: value}}
    """
    if not labels:
        return None, "labels 不能为空"

    data = []
    for i, row_label in enumerate(labels):
        for j, col_label in enumerate(labels):
            val = matrix.get(row_label, {}).get(col_label, 0)
            data.append([j, i, val])

    from backend.clinical_evidence import methodology

    return {
        "labels": labels,
        "data": data,
        "x_axis": labels,
        "y_axis": labels,
        "methodology": methodology(
            "comorbidity",
            ["borsboom_cramer_2013"],
            caveat="热图展示共现频数/强度，颜色深浅不代表因果方向。",
        ),
    }, None


def generate_network_data(
    edges: List[Dict[str, Any]],
    nodes: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    生成网络图 JSON 数据。
    edges: [{source, target, weight}]
    nodes: [{id, name, size}] 如不提供则自动从 edges 推导
    """
    if not edges:
        return None, "edges 不能为空"

    if nodes is None:
        node_ids = set()
        for e in edges:
            node_ids.add(e.get("source", ""))
            node_ids.add(e.get("target", ""))
        nodes = [{"id": nid, "name": nid, "size": 10} for nid in sorted(node_ids)]

    from backend.clinical_evidence import methodology

    return {
        "nodes": nodes,
        "edges": edges,
        "methodology": methodology(
            "comorbidity",
            ["borsboom_cramer_2013"],
            caveat="网络图边表示共现或统计关联，不能解释为病理传播路径。",
        ),
    }, None


def build_network_from_matrix(matrix_result: dict) -> Tuple[Optional[dict], Optional[str]]:
    """从共病矩阵结果构建网络图 edges/nodes。"""
    labels = matrix_result.get("diagnoses") or []
    freq = matrix_result.get("frequency_matrix") or {}
    if len(labels) < 2:
        # 单诊断队列：用共现对构造最小网络
        counts = matrix_result.get("diagnosis_counts") or {}
        if len(counts) >= 2:
            labels = sorted(counts.keys())[:6]
            freq = {d1: {d2: (counts.get(d1, 0) + counts.get(d2, 0)) / 2 for d2 in labels} for d1 in labels}
            matrix_result = {**matrix_result, "diagnoses": labels, "frequency_matrix": freq}
        else:
            return None, "诊断种类不足（队列中至少需要 2 种诊断，可扩大患者 ID 范围如 P001–P030）"
    edges = []
    for i, d1 in enumerate(labels):
        for d2 in labels[i + 1:]:
            w = float(freq.get(d1, {}).get(d2, 0) or 0)
            if w > 0:
                edges.append({"source": d1, "target": d2, "weight": w})
    if not edges:
        return None, "无共现边"
    nodes = [{"id": d, "label": d, "size": int(matrix_result.get("diagnosis_counts", {}).get(d, 1))} for d in labels]
    return generate_network_data(edges, nodes)


def analyze_cohort_convenience(
    analysis_type: str,
    cohort_ids: List[str],
    primary_diagnosis: Optional[str] = None,
    n_clusters: int = 3,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """UI 便捷入口：按分析类型自动串联 matrix/spectrum/cluster/heatmap/network。"""
    if analysis_type == "共病矩阵":
        return compute_comorbidity_matrix(cohort_ids, owner_user_id=owner_user_id)
    if analysis_type == "谱系分析":
        primary = (primary_diagnosis or "").strip() or infer_primary_diagnosis(cohort_ids, owner_user_id) or "depression"
        resp, err = analyze_spectrum_relationship(primary, cohort_ids, owner_user_id=owner_user_id)
        if err:
            return None, err
        return _json_safe(resp), None
    if analysis_type == "聚类":
        return cluster_comorbidity_patterns(cohort_ids, n_clusters, owner_user_id=owner_user_id)
    matrix_result, err = compute_comorbidity_matrix(cohort_ids, owner_user_id=owner_user_id)
    if err:
        return None, err
    labels = matrix_result.get("diagnoses") or []
    freq = matrix_result.get("frequency_matrix") or {}
    if analysis_type == "热图":
        return generate_heatmap_data(freq, labels)
    if analysis_type == "网络图":
        net, nerr = build_network_from_matrix(matrix_result)
        if nerr:
            return None, nerr
        return net, None
    return None, f"未知分析类型: {analysis_type}"
