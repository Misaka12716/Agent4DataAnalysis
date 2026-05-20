"""Compact, LLM-friendly summary of the solver catalog.

Used to teach the planner *which* operators exist and *what shape*
each one expects.  Kept short on purpose so that the planner prompt
stays well under the model's context budget.

中文说明
========
把 ``registry.list_solvers`` 里的算子压成「能力分桶 + 每算子的角色签名」，
再渲染成 markdown 塞进规划 prompt。故意短：tokens 预算有限；详细 I/O 仍以
各 solver 的 ``SolverContract`` 为准。
"""
from __future__ import annotations

from typing import Any, Dict, List

from distillation.software1_pipeline_demo_app.registry import (
    list_solvers,
    make_solver,
)


# ---------------------------------------------------------------------------
# Capability buckets — guides the planner about *when* to reach for what.
# These are short, human-curated tags; they do not constrain the catalog.
# ---------------------------------------------------------------------------
_BUCKETS: List[Dict[str, Any]] = [
    {
        "tag": "data_governance",
        "label": "数据治理 / 清洗 / 质量评估",
        "members": ["metadata_parser", "missing_summary", "fillna_median",
                     "outlier_iqr_flag", "consistency_check"],
    },
    {
        "tag": "descriptive",
        "label": "描述性统计 / 分布",
        "members": ["describe_full", "distribution_histogram",
                     "normality_test"],
    },
    {
        "tag": "association",
        "label": "相关性 / 关联性 / 多重比较",
        "members": ["pearson_correlation", "spearman_correlation",
                     "kendall_correlation", "association_rules",
                     "multiple_correction"],
    },
    {
        "tag": "hypothesis",
        "label": "差异性检验 / 组间比较",
        "members": ["welch_t_test", "mann_whitney_u_test",
                     "chi_square_independence", "oneway_anova",
                     "kruskal_wallis"],
    },
    {
        "tag": "prediction_classical",
        "label": "经典分类 / 回归建模（含 CV）",
        "members": ["logistic_regression", "random_forest",
                     "hist_gradient_boosting", "xgboost", "lightgbm",
                     "svm_rbf", "knn_k_selection"],
    },
    {
        "tag": "survival",
        "label": "生存分析 / 风险预警",
        "members": ["cox_regression", "propensity_score_matching"],
    },
    {
        "tag": "clinical_specific",
        "label": "临床专用：参考区间、PANSS、纵向特征、文本",
        "members": ["reference_range_flag", "panss_factor_score",
                     "panss_trajectory_responder",
                     "time_series_features", "text_features"],
    },
    {
        "tag": "bioinformatics",
        "label": "生信专用：SOFT 解析 / 探针-基因聚合（表达层 & DEG 层） / DEG / PCA / 聚类 / 通路富集",
        "members": ["gds_soft_parser", "probe_to_gene_collapse",
                     "probe_deg_collapse_to_gene",
                     "limma_deg_two_group", "pca_decompose",
                     "hclust_samples", "pathway_enrichment_fisher"],
    },
]


def _solver_signature(solver_id: str) -> Dict[str, Any]:
    """Return roles + static_params + output_files for one solver."""
    try:
        s = make_solver(solver_id)
    except Exception as e:
        return {"id": solver_id, "error": f"{type(e).__name__}: {e}"}
    c = s.contract
    roles = {}
    for k, spec in c.roles.items():
        roles[k] = {
            "role": spec.role.value,
            "optional": spec.optional,
            "desc": spec.description,
        }
    return {
        "id": solver_id,
        "name": c.name,
        "capability": c.capability,
        "description": c.description,
        "roles": roles,
        "outputs": dict(c.output_files),
        "default_params": dict(c.static_params),
    }


def build_catalog_for_planner() -> List[Dict[str, Any]]:
    """List of compact solver descriptors for the planner LLM."""
    out: List[Dict[str, Any]] = []
    for sid, _desc in list_solvers():
        sig = _solver_signature(sid)
        out.append(sig)
    return out


def render_catalog_markdown(catalog: List[Dict[str, Any]],
                              max_chars: int = 10000) -> str:
    """Pretty, prompt-ready markdown rendering of the catalog grouped
    by buckets.  Trims at ``max_chars`` so we stay polite with token
    budget.
    """
    by_id = {c["id"]: c for c in catalog}

    lines: List[str] = []
    seen: set = set()
    for bucket in _BUCKETS:
        lines.append(f"### {bucket['label']}  ({bucket['tag']})")
        for sid in bucket["members"]:
            c = by_id.get(sid)
            if c is None:
                continue
            seen.add(sid)
            lines.append(f"- `{sid}` — {c.get('description','')}")
            roles = c.get("roles") or {}
            if roles:
                role_bits = []
                for rk, rv in roles.items():
                    optstr = "?" if rv.get("optional") else ""
                    role_bits.append(f"{rk}{optstr}:{rv['role']}")
                lines.append(f"    roles: {', '.join(role_bits)}")
            outs = c.get("outputs") or {}
            if outs:
                lines.append(f"    outputs: {', '.join(outs.keys())}")
        lines.append("")

    leftover = [c for c in catalog if c["id"] not in seen]
    if leftover:
        lines.append("### 其他")
        for c in leftover:
            lines.append(f"- `{c['id']}` — {c.get('description','')}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (catalog truncated)"
    return text
