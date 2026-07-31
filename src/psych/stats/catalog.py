# psych/stats/catalog.py — 一键统计分析方法目录

from __future__ import annotations

from typing import Any, Dict, List

STATS_METHODS: Dict[str, Dict[str, Any]] = {
    "describe_full": {
        "solver_id": "describe_full",
        "name_zh": "描述性统计",
        "category": "descriptive",
        "params_schema": {
            "numeric_columns": {"type": "array", "items": "string", "required": False},
        },
    },
    "groupby_stat": {
        "solver_id": "groupby_stat",
        "name_zh": "分组统计",
        "category": "descriptive",
        "params_schema": {
            "stat": {"type": "string", "default": "mean"},
            "group_col": {"type": "string", "role": True},
            "value_col": {"type": "string", "role": True},
        },
    },
    "pearson_correlation": {
        "solver_id": "pearson_correlation",
        "name_zh": "Pearson相关",
        "category": "correlation",
        "params_schema": {},
    },
    "spearman_correlation": {
        "solver_id": "spearman_correlation",
        "name_zh": "Spearman相关",
        "category": "correlation",
        "params_schema": {},
    },
    "kendall_correlation": {
        "solver_id": "kendall_correlation",
        "name_zh": "Kendall相关",
        "category": "correlation",
        "params_schema": {},
    },
    "welch_t_test": {
        "solver_id": "welch_t_test",
        "name_zh": "Welch t检验",
        "category": "difference",
        "params_schema": {},
    },
    "mann_whitney_u_test": {
        "solver_id": "mann_whitney_u_test",
        "name_zh": "Mann-Whitney U检验",
        "category": "difference",
        "params_schema": {},
    },
    "chi_square_independence": {
        "solver_id": "chi_square_independence",
        "name_zh": "卡方独立性检验",
        "category": "difference",
        "params_schema": {},
    },
    "oneway_anova": {
        "solver_id": "oneway_anova",
        "name_zh": "单因素方差分析",
        "category": "difference",
        "params_schema": {},
    },
    "kruskal_wallis": {
        "solver_id": "kruskal_wallis",
        "name_zh": "Kruskal-Wallis检验",
        "category": "difference",
        "params_schema": {},
    },
    "normality_test": {
        "solver_id": "normality_test",
        "name_zh": "正态性检验",
        "category": "descriptive",
        "params_schema": {},
    },
    "proportion_ci": {
        "solver_id": "proportion_ci",
        "name_zh": "比例置信区间",
        "category": "descriptive",
        "params_schema": {},
    },
}


def list_stats_methods() -> List[Dict[str, Any]]:
    return [{"method_id": mid, **meta} for mid, meta in STATS_METHODS.items()]


def resolve_solver_id(method_id: str) -> str:
    meta = STATS_METHODS.get(method_id)
    if not meta:
        raise KeyError(f"未知统计方法: {method_id}")
    return str(meta["solver_id"])
