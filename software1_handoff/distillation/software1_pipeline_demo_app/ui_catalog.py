"""UI hints for the drag-and-drop demo (which numeric fields each solver exposes)."""
from __future__ import annotations

from typing import Any, Dict, List

# Optional per-solver parameter rows (merged into ``params`` for ``make_solver``).
SOLVER_PARAM_FIELDS: Dict[str, List[Dict[str, Any]]] = {
    "outlier_iqr_flag": [
        {"key": "k", "type": "number", "default": 1.5, "label": "IQR 倍数 k", "step": "0.1"},
    ],
    "distribution_histogram": [
        {"key": "n_bins", "type": "number", "default": 20, "label": "分箱数", "step": "1"},
    ],
    "normality_test": [
        {"key": "alpha", "type": "number", "default": 0.05, "label": "显著性 α", "step": "0.01"},
    ],
    "multiple_correction": [
        {"key": "alpha", "type": "number", "default": 0.05, "label": "显著性 α", "step": "0.01"},
    ],
    "association_rules": [
        {"key": "min_support", "type": "number", "default": 0.05, "label": "min_support", "step": "0.01"},
        {"key": "min_confidence", "type": "number", "default": 0.3, "label": "min_confidence", "step": "0.05"},
    ],
    "panss_trajectory_responder": [
        {"key": "responder_threshold_pct", "type": "number", "default": 30, "label": "应答阈值 %", "step": "1"},
    ],
    "probe_to_gene_collapse": [
        {"key": "method", "type": "text", "default": "max", "label": "聚合方法 (max/mean/median)"},
        {"key": "gene_symbol_col", "type": "text", "default": "Gene symbol", "label": "annotation 中基因列名"},
    ],
    "limma_deg_two_group": [
        {"key": "moderation", "type": "text", "default": "true", "label": "EB 方差收缩 (true/false)"},
        {"key": "group_field", "type": "text", "default": "group_description", "label": "分组列 (group / group_description)"},
    ],
    "pca_decompose": [
        {"key": "n_components", "type": "number", "default": 5, "label": "主成分数", "step": "1"},
        {"key": "standardize", "type": "text", "default": "true", "label": "z-score 标准化 (true/false)"},
    ],
    "hclust_samples": [
        {"key": "method", "type": "text", "default": "average", "label": "linkage (single/complete/average/ward)"},
        {"key": "metric", "type": "text", "default": "correlation", "label": "metric (correlation/euclidean/...)"},
        {"key": "n_clusters", "type": "number", "default": 2, "label": "簇数 k", "step": "1"},
    ],
    "pathway_enrichment_fisher": [
        {"key": "top_k", "type": "number", "default": 200, "label": "目标基因 top-K", "step": "10"},
        {"key": "min_overlap", "type": "number", "default": 2, "label": "最小重合数", "step": "1"},
    ],
}


def build_solver_catalog() -> List[Dict[str, Any]]:
    from distillation.software1_pipeline_demo_app.registry import list_solvers

    out = []
    for sid, desc in list_solvers():
        out.append({
            "id": sid,
            "desc": desc,
            "params": SOLVER_PARAM_FIELDS.get(sid, []),
        })
    return out
