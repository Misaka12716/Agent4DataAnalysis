"""UI hints for the drag-and-drop demo (which numeric fields each solver exposes)."""
from __future__ import annotations

from typing import Any, Dict, List

# Optional per-solver parameter rows (merged into `params` for `make_solver`).
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
    "mediation_analysis": [
        {"key": "n_bootstrap", "type": "number", "default": 1000, "label": "Bootstrap 次数", "step": "100"},
        {"key": "ci_level", "type": "number", "default": 0.95, "label": "置信水平", "step": "0.01"},
    ],
    "pgx_interaction": [
        {"key": "outcome_type", "type": "text", "default": "binary", "label": "outcome 类型 (binary/continuous)"},
        {"key": "snp_coding", "type": "text", "default": "additive", "label": "SNP 编码 (additive/dominant/recessive)"},
        {"key": "multiple_test", "type": "text", "default": "fdr_bh", "label": "多重检验 (fdr_bh/bonferroni)"},
        {"key": "alpha", "type": "number", "default": 0.05, "label": "显著性 alpha", "step": "0.01"},
    ],
    "mendelian_randomization": [
        {"key": "random_state", "type": "number", "default": 42, "label": "随机种子", "step": "1"},
    ],
    "edger_de": [
        {"key": "test_method", "type": "text", "default": "qlf", "label": "检验 (qlf/lrt/exact)"},
        {"key": "group_field", "type": "text", "default": "group_description", "label": "分组列"},
        {"key": "alpha", "type": "number", "default": 0.05, "label": "显著性 alpha", "step": "0.01"},
        {"key": "min_count", "type": "number", "default": 10, "label": "最小 count", "step": "1"},
        {"key": "min_total_count", "type": "number", "default": 15, "label": "最小总 count", "step": "1"},
    ],
    "deseq2_de": [
        {"key": "alpha", "type": "number", "default": 0.05, "label": "显著性 alpha", "step": "0.01"},
        {"key": "lfc_threshold", "type": "number", "default": 0.0, "label": "logFC 阈值", "step": "0.1"},
        {"key": "min_count", "type": "number", "default": 10, "label": "最小 count", "step": "1"},
    ],
    "combat_batch_correction": [
        {"key": "data_type", "type": "text", "default": "microarray", "label": "数据类型 (microarray/rnaseq)"},
        {"key": "par_prior", "type": "text", "default": "true", "label": "参数先验 (true/false)"},
        {"key": "mean_only", "type": "text", "default": "false", "label": "仅均值校正 (true/false)"},
    ],
    # V8 additions
    "network_meta_analysis": [
        {"key": "reference_treatment", "type": "text", "default": "", "label": "参考治疗 (留空=自动选最常用)"},
        {"key": "smaller_is_better", "type": "text", "default": "true", "label": "效应越小越好 (true/false)"},
        {"key": "n_sim_sucra", "type": "number", "default": 5000, "label": "SUCRA Monte Carlo 次数", "step": "500"},
    ],
    "irt_calibration": [
        {"key": "min_obs_per_item", "type": "number", "default": 30, "label": "每个 item 最小观测数", "step": "5"},
    ],
    "latent_growth_curve": [
        {"key": "min_obs_per_subject", "type": "number", "default": 3, "label": "每个受试最小观测", "step": "1"},
    ],
    "prs_x_env_interaction": [
        {"key": "outcome_type", "type": "text", "default": "linear", "label": "outcome 类型 (linear/logistic)"},
        {"key": "standardize_prs", "type": "text", "default": "true", "label": "PRS 标准化 (true/false)"},
        {"key": "center_env", "type": "text", "default": "true", "label": "环境变量去中心 (true/false)"},
    ],
    "bayesian_hierarchical_glm": [
        {"key": "min_group_size", "type": "number", "default": 2, "label": "组内最小样本", "step": "1"},
    ],
    "ordinal_regression": [
        {"key": "link", "type": "text", "default": "logit", "label": "链接函数 (logit/probit)"},
    ],
    "g_formula_tmle": [
        {"key": "n_bootstrap", "type": "number", "default": 1000, "label": "Bootstrap 次数", "step": "100"},
        {"key": "trim_ps", "type": "number", "default": 0.02, "label": "倾向得分剪裁 trim", "step": "0.01"},
    ],
    "symptom_network_analysis": [
        {"key": "min_obs", "type": "number", "default": 100, "label": "最小样本数", "step": "10"},
        {"key": "standardize", "type": "text", "default": "true", "label": "z-score 标准化 (true/false)"},
    ],
    "joint_longitudinal_survival": [
        {"key": "min_obs_per_subject", "type": "number", "default": 2, "label": "每个受试纵向最小次", "step": "1"},
    ],
    "disparate_impact_audit": [
        {"key": "threshold", "type": "number", "default": 0.5, "label": "概率→0/1 阈值", "step": "0.05"},
        {"key": "favorable_label", "type": "number", "default": 1, "label": "有利标签 (1/0)", "step": "1"},
        {"key": "di_pass_ratio", "type": "number", "default": 0.8, "label": "DI 通过阈值 (默认 4/5)", "step": "0.05"},
    ],
}


def build_solver_catalog() -> List[Dict[str, Any]]:
    from operator_pipeline.registry import list_solvers

    out = []
    for sid, desc in list_solvers():
        out.append({
            "id": sid,
            "desc": desc,
            "params": SOLVER_PARAM_FIELDS.get(sid, []),
        })
    return out
