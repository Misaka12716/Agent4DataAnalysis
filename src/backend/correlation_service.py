# backend/correlation_service.py
# 指标相关性分析与可视化 — Pearson/Spearman/Kendall + 偏相关 + 热图数据

import json
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from utils.mysql_utils import mysql_handler


def compute_correlation(
    data: List[Dict],
    columns: List[str],
    method: str = "pearson",
) -> Tuple[Optional[dict], Optional[str]]:
    """
    计算指标相关性矩阵。
    data: [{col1: val, col2: val, ...}, ...]
    columns: 要计算相关的列名列表
    method: pearson | spearman | kendall
    return: {matrix: [[r11, r12, ...], ...], p_values: [[p11, p12, ...], ...], labels: [...], method: str}
    """
    if not data or len(data) < 3:
        return None, "数据不足（至少需要 3 条记录）"
    if len(columns) < 2:
        return None, "至少需要 2 个指标"
    if method not in ("pearson", "spearman", "kendall"):
        return None, "method 必须为 pearson/spearman/kendall"

    df = pd.DataFrame(data)
    valid_cols = [c for c in columns if c in df.columns]
    if len(valid_cols) < 2:
        return None, f"有效列不足: {valid_cols}"

    n = len(valid_cols)
    corr_matrix = [[1.0] * n for _ in range(n)]
    p_matrix: List[List[Optional[float]]] = [[None] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            col_i = df[valid_cols[i]].dropna()
            col_j = df[valid_cols[j]].dropna()
            # 对齐索引
            mask = df[valid_cols[i]].notna() & df[valid_cols[j]].notna()
            x = df.loc[mask, valid_cols[i]].values
            y = df.loc[mask, valid_cols[j]].values
            if len(x) < 3:
                corr_matrix[i][j] = None
                p_matrix[i][j] = None
                continue

            if method == "pearson":
                r, p = scipy_stats.pearsonr(x, y)
            elif method == "spearman":
                r, p = scipy_stats.spearmanr(x, y)
            else:
                r, p = scipy_stats.kendalltau(x, y)

            r_val = round(float(r), 4) if r is not None and not (isinstance(r, float) and (r != r)) else None
            p_val = round(float(p), 4) if p is not None and not (isinstance(p, float) and (p != p)) else None
            corr_matrix[i][j] = r_val
            p_matrix[i][j] = p_val

    from backend.clinical_evidence import methodology

    method_evidence = {
        "pearson": ["pearson_1895"],
        "spearman": ["spearman_1904"],
        "kendall": ["kendall_1938"],
    }
    return {
        "matrix": corr_matrix,
        "p_values": p_matrix,
        "labels": valid_cols,
        "method": method,
        "sample_size": len(df),
        "methodology": methodology(
            "correlation",
            method_evidence.get(method, []),
            caveat="相关分析只量化变量间关联；显著相关不代表因果关系，需考虑多重比较、混杂和样本量。",
        ),
    }, None


def partial_correlation(
    data: List[Dict],
    columns: List[str],
    control_vars: List[str],
) -> Tuple[Optional[dict], Optional[str]]:
    """
    偏相关分析：控制 control_vars 后计算 columns 间的相关性。
    使用线性回归残差方法。
    """
    if not data or len(data) < 5:
        return None, "数据不足（至少需要 5 条记录）"
    if len(columns) < 2:
        return None, "至少需要 2 个指标"
    if not control_vars:
        return None, "至少需要 1 个控制变量"

    df = pd.DataFrame(data)
    all_cols = columns + control_vars
    all_cols = [c for c in all_cols if c in df.columns]
    if len(all_cols) < len(columns) + 1:
        return None, "有效列不足"

    from sklearn.linear_model import LinearRegression
    n = len(columns)
    partial_matrix = [[1.0] * n for _ in range(n)]
    p_matrix = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            col_i = columns[i]
            col_j = columns[j]
            if col_i not in df.columns or col_j not in df.columns:
                partial_matrix[i][j] = None
                continue

            # 用 control_vars 回归 col_i 和 col_j，取残差再算相关
            ctrl_cols = [c for c in control_vars if c in df.columns]
            if not ctrl_cols:
                # 无控制变量，退化为普通相关
                r, p = scipy_stats.pearsonr(df[col_i].dropna(), df[col_j].dropna())
                partial_matrix[i][j] = round(float(r), 4)
                p_matrix[i][j] = round(float(p), 4)
                continue

            valid = df[[col_i, col_j] + ctrl_cols].dropna()
            if len(valid) < 5:
                partial_matrix[i][j] = None
                continue

            X_ctrl = valid[ctrl_cols].values
            y_i = valid[col_i].values
            y_j = valid[col_j].values

            try:
                reg_i = LinearRegression().fit(X_ctrl, y_i)
                resid_i = y_i - reg_i.predict(X_ctrl)
                reg_j = LinearRegression().fit(X_ctrl, y_j)
                resid_j = y_j - reg_j.predict(X_ctrl)
                r, p = scipy_stats.pearsonr(resid_i, resid_j)
                partial_matrix[i][j] = round(float(r), 4)
                p_matrix[i][j] = round(float(p), 4)
            except Exception:
                partial_matrix[i][j] = None
                p_matrix[i][j] = None

    from backend.clinical_evidence import methodology

    return {
        "matrix": partial_matrix,
        "p_values": p_matrix,
        "labels": columns,
        "control_vars": control_vars,
        "sample_size": len(df),
        "methodology": methodology(
            "correlation",
            ["pearson_1895"],
            caveat="偏相关通过线性回归残差控制指定变量，不能排除未测量混杂或非线性关系。",
        ),
    }, None


def correlation_heatmap_data(
    matrix: List[List[float]],
    labels: List[str],
    p_values: Optional[List[List[float]]] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """生成热图 JSON 数据（兼容 echarts）。"""
    if not matrix or not labels:
        return None, "matrix 和 labels 不能为空"

    data = []
    for i, row_label in enumerate(labels):
        for j, col_label in enumerate(labels):
            r_val = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else None
            p_val = p_values[i][j] if p_values and i < len(p_values) and j < len(p_values[i]) else None
            data.append({
                "x": col_label,
                "y": row_label,
                "value": r_val,
                "p_value": p_val,
                "significant": p_val is not None and p_val < 0.05,
            })

    from backend.clinical_evidence import methodology

    return {
        "labels": labels,
        "data": data,
        "methodology": methodology(
            "correlation",
            caveat="热图是相关矩阵的可视化表达，不应单独作为临床结论。",
        ),
    }, None


def _benjamini_hochberg(p_vals: List[float]) -> List[float]:
    """Benjamini-Hochberg FDR 校正，返回与输入等长的 q 值列表。

    参考: Benjamini Y, Hochberg Y (1995) J R Stat Soc B 57:289-300.
    """
    m = len(p_vals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_vals[i])
    q_sorted = [0.0] * m
    prev = 1.0
    for rank, idx in enumerate(reversed(order)):
        i = m - rank
        p = p_vals[idx]
        q = min(prev, p * m / i)
        q_sorted[idx] = q
        prev = q
    return q_sorted


def find_significant_pairs(
    matrix: List[List[float]],
    labels: List[str],
    p_values: Optional[List[List[float]]] = None,
    threshold: float = 0.05,
    min_abs_r: float = 0.3,
    correction: str = "fdr_bh",
) -> Tuple[Optional[list], Optional[str]]:
    """找出显著相关（|r| >= min_abs_r 且校正后 q < threshold）的指标对。

    默认对同一矩阵内的多重检验做 Benjamini-Hochberg FDR 校正（correction="fdr_bh"），
    避免相关对数量增多时假阳性率升高；correction="none" 可回退到未校正 p 值判定。
    """
    from backend.clinical_evidence import methodology

    if not matrix or not labels:
        return None, "matrix 和 labels 不能为空"

    n = len(labels)
    candidates = []
    for i in range(n):
        for j in range(i + 1, n):
            r_val = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else None
            p_val = p_values[i][j] if p_values and i < len(p_values) and j < len(p_values[i]) else None
            if r_val is None or abs(r_val) < min_abs_r:
                continue
            candidates.append({"indicator_1": labels[i], "indicator_2": labels[j], "correlation": r_val, "p_value": p_val})

    testable = [c for c in candidates if c["p_value"] is not None]
    if correction == "fdr_bh" and len(testable) > 1:
        q_vals = _benjamini_hochberg([c["p_value"] for c in testable])
        for c, q in zip(testable, q_vals):
            c["q_value"] = round(float(q), 4)
    else:
        for c in testable:
            c["q_value"] = c["p_value"]

    pairs = []
    for c in candidates:
        q_val = c.get("q_value")
        significant = q_val is not None and q_val < threshold
        pairs.append({
            "indicator_1": c["indicator_1"],
            "indicator_2": c["indicator_2"],
            "correlation": c["correlation"],
            "p_value": c["p_value"],
            "q_value": q_val,
            "correction_method": correction if c["p_value"] is not None else None,
            "significant": significant,
        })

    pairs.sort(key=lambda x: abs(x["correlation"] or 0), reverse=True)
    return {
        "pairs": pairs,
        "correction_method": correction,
        "n_tests": len(testable),
        "methodology": methodology(
            "correlation",
            ["benjamini_hochberg_1995"] if correction == "fdr_bh" else [],
            caveat="显著相关对按阈值筛选；当同一矩阵存在多组检验时默认使用 Benjamini-Hochberg FDR 校正 q 值判定显著性，而非原始 p 值。",
        ),
    }, None
