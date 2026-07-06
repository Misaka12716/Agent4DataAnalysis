# worker/mental_health_analytics.py
# 精神科定量分析函数集 — 供 Worker 沙箱/Coder 调用
# 统一签名：def xxx(df: pd.DataFrame, **params) -> Dict[str, Any]
# 返回 {"data": ..., "metadata": ...}

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from scipy import stats
import warnings

warnings.filterwarnings("ignore")


def data_distribution(
    df: pd.DataFrame,
    columns: Optional[List[str]] = None,
    **params
) -> Dict[str, Any]:
    """
    描述统计：均值、标准差、四分位数、缺失率。
    若不指定 columns，默认对除 patient_id 外的数值列计算。
    """
    if columns is None:
        num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        columns = [c for c in num_cols if c != "patient_id"]
    if not columns:
        return {"data": [], "metadata": {"columns": [], "row_count": len(df), "error": "无可分析的数值列"}}

    desc = df[columns].describe(percentiles=[0.25, 0.5, 0.75]).T
    missing = df[columns].isnull().sum().to_frame("missing_count")
    missing_rate = (df[columns].isnull().mean() * 100).to_frame("missing_pct")

    result = []
    for col in columns:
        row = {
            "column": col,
            "count": int(desc.loc[col, "count"]) if col in desc.index else 0,
            "mean": round(float(desc.loc[col, "mean"]), 4) if col in desc.index else None,
            "std": round(float(desc.loc[col, "std"]), 4) if col in desc.index else None,
            "min": round(float(desc.loc[col, "min"]), 4) if col in desc.index else None,
            "q25": round(float(desc.loc[col, "25%"]), 4) if col in desc.index else None,
            "median": round(float(desc.loc[col, "50%"]), 4) if col in desc.index else None,
            "q75": round(float(desc.loc[col, "75%"]), 4) if col in desc.index else None,
            "max": round(float(desc.loc[col, "max"]), 4) if col in desc.index else None,
            "missing_count": int(missing.loc[col, "missing_count"]) if col in missing.index else 0,
            "missing_pct": round(float(missing_rate.loc[col, "missing_pct"]), 2) if col in missing_rate.index else 0.0,
        }
        result.append(row)

    return {
        "data": result,
        "metadata": {
            "columns": columns,
            "row_count": len(df),
            "method": "describe_with_missing",
        }
    }


def scale_trend(
    df: pd.DataFrame,
    scales: List[str],
    time_points: Optional[List[str]] = None,
    time_col: str = "visit_date",
    group_col: str = "patient_id",
    **params
) -> Dict[str, Any]:
    """
    量表变化趋势分析：按时间点计算各量表均值。
    time_points: 时间窗口标签列表，如 ["baseline","week4","week8"]
    如果 time_col 是日期类型且 time_points 为 None，则按时间排序后取该列唯一值作为时间点。
    返回趋势表 + 折线图数据（series 格式，前端可直接绘图）。
    """
    if time_points is None:
        if time_col in df.columns:
            sorted_times = sorted(df[time_col].dropna().unique())
            time_points = [str(t) for t in sorted_times]
        else:
            return {"data": {}, "metadata": {"error": "time_col 不存在"}}

    # 验证 scale 列存在
    valid_scales = [s for s in scales if s in df.columns]
    if not valid_scales:
        return {"data": {}, "metadata": {"error": "无有效量表列", "scales_requested": scales}}

    # 按时间点分组计算各量表均值
    trend_table = []
    chart_series = {s: [] for s in valid_scales}
    chart_x = []

    for tp in time_points:
        if time_col in df.columns and tp in df[time_col].astype(str).values:
            subset = df[df[time_col].astype(str) == tp]
        else:
            subset = df
        row = {"time_point": tp, "n": len(subset)}
        for s in valid_scales:
            vals = subset[s].dropna()
            mean_v = round(float(vals.mean()), 4) if len(vals) > 0 else None
            sd_v = round(float(vals.std()), 4) if len(vals) > 0 else None
            row[s + "_mean"] = mean_v
            row[s + "_sd"] = sd_v
            chart_series[s].append(mean_v)
        trend_table.append(row)
        chart_x.append(tp)

    return {
        "data": {
            "trend_table": trend_table,
            "chart": {
                "x": chart_x,
                "series": chart_series,
            }
        },
        "metadata": {
            "scales": valid_scales,
            "time_points": time_points,
            "method": "mean_trend",
        }
    }


def medication_outcome_assoc(
    df: pd.DataFrame,
    medications: List[str],
    outcome_measure: str = "response_rate",
    med_col: str = "medication",
    outcome_col: str = "outcome",
    positive_outcome: str = "response",
    **params
) -> Dict[str, Any]:
    """
    用药与结局关联分析：计算各药物的有效率、OR 值、95% CI。
    需要 df 包含 med_col（用药名称）和 outcome_col（结局标签）。
    返回对比表。
    """
    if med_col not in df.columns or outcome_col not in df.columns:
        return {"data": {}, "metadata": {"error": "用药或结局列不存在"}}

    result = []
    df_valid = df[[med_col, outcome_col]].dropna()

    for med in medications:
        subset = df_valid[df_valid[med_col].astype(str).str.contains(med, case=False, na=False)]
        total = len(subset)
        if total == 0:
            result.append({
                "medication": med,
                "total": 0,
                "responders": 0,
                f"{outcome_measure}": None,
                "error": "无数据",
            })
            continue
        responders = int((subset[outcome_col].astype(str) == positive_outcome).sum())
        rate = round(responders / total, 4) if total > 0 else None
        result.append({
            "medication": med,
            "total": total,
            "responders": responders,
            outcome_measure: rate,
        })

    # 计算两两 OR 值（仅对有两组数据的药物）
    for i in range(len(result)):
        for j in range(i + 1, len(result)):
            a = result[i]
            b = result[j]
            if a["total"] == 0 or b["total"] == 0:
                continue
            # 2x2 列联表
            table = np.array([
                [a["responders"], a["total"] - a["responders"]],
                [b["responders"], b["total"] - b["responders"]],
            ])
            try:
                odds_ratio, p_value = stats.fisher_exact(table)
                # 手动计算 CI（Woolf 方法近似）
                log_or = np.log(odds_ratio) if odds_ratio > 0 else 0
                se_log_or = np.sqrt(1 / table[0, 0] + 1 / table[0, 1] + 1 / table[1, 0] + 1 / table[1, 1]) if np.all(table > 0) else np.nan
                ci_low = round(np.exp(log_or - 1.96 * se_log_or), 4) if not np.isnan(se_log_or) else None
                ci_high = round(np.exp(log_or + 1.96 * se_log_or), 4) if not np.isnan(se_log_or) else None
                result[i][f"vs_{b['medication']}_OR"] = round(odds_ratio, 4)
                result[i][f"vs_{b['medication']}_OR_95CI"] = [ci_low, ci_high]
                result[i][f"vs_{b['medication']}_p"] = round(p_value, 4)
            except Exception:
                continue

    return {
        "data": result,
        "metadata": {
            "medications": medications,
            "outcome_measure": outcome_measure,
            "positive_outcome": positive_outcome,
            "method": "fisher_exact",
        }
    }


def relapse_analysis(
    df: pd.DataFrame,
    follow_up_days: int = 365,
    event_col: str = "relapse",
    date_col: str = "relapse_date",
    **params
) -> Dict[str, Any]:
    """
    复发/再入院分析：计算复发率、平均复发时间。
    若数据含复发日期列，输出简单 Kaplan-Meier 风格数据（时间→累计未复发率）。
    """
    total = len(df)
    if total == 0:
        return {"data": {}, "metadata": {"error": "数据为空"}}

    relapse_count = 0
    if event_col in df.columns:
        relapse_count = int((df[event_col].astype(str).str.lower().isin(["1", "yes", "true", "y"]) | (df[event_col] == 1)).sum())

    relapse_rate = round(relapse_count / total, 4) if total > 0 else 0.0

    result = {
        "total_patients": total,
        "relapse_count": relapse_count,
        "relapse_rate": relapse_rate,
        "follow_up_days": follow_up_days,
    }

    # 简易 KM 生存数据
    km_data = []
    if date_col in df.columns and event_col in df.columns:
        df_km = df[[date_col, event_col]].copy()
        df_km["is_event"] = df_km[event_col].astype(str).str.lower().isin(["1", "yes", "true", "y"]) | (df_km[event_col] == 1)
        df_km[date_col] = pd.to_datetime(df_km[date_col], errors="coerce")
        df_km = df_km.dropna(subset=[date_col])

        if len(df_km) > 1:
            df_km = df_km.sort_values(date_col)
            at_risk = len(df_km)
            for _, row in df_km.iterrows():
                km_data.append({
                    "time": str(row[date_col].date()) if pd.notna(row[date_col]) else None,
                    "at_risk": at_risk,
                    "event": 1 if row["is_event"] else 0,
                })
                if row["is_event"]:
                    at_risk -= 1
        result["km_data"] = km_data

    return {
        "data": result,
        "metadata": {
            "follow_up_days": follow_up_days,
            "method": "relapse_rate_km",
        }
    }


def multimodal_summary(
    df: pd.DataFrame,
    text_records: Optional[List[str]] = None,
    image_annotations: Optional[List[Dict]] = None,
    **params
) -> Dict[str, Any]:
    """
    多模态数据汇总：对表格 + 文本 + 影像标注进行统一摘要。
    表格部分输出列/类型/行数；文本部分输出数量和长度分布；影像部分输出数量和标签分布。
    """
    summary = {
        "tabular": {
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns],
        },
        "text": {
            "record_count": len(text_records) if text_records else 0,
            "avg_length": round(np.mean([len(t) for t in text_records]), 1) if text_records else 0,
            "max_length": max([len(t) for t in text_records]) if text_records else 0,
        },
        "images": {
            "count": len(image_annotations) if image_annotations else 0,
            "labels": {},
        },
    }

    if image_annotations:
        label_counts = {}
        for ann in image_annotations:
            label = ann.get("label", "unknown")
            label_counts[label] = label_counts.get(label, 0) + 1
        summary["images"]["labels"] = label_counts

    return {
        "data": summary,
        "metadata": {"method": "multimodal_summary"},
    }


__all__ = [
    "data_distribution",
    "scale_trend",
    "medication_outcome_assoc",
    "relapse_analysis",
    "multimodal_summary",
]
