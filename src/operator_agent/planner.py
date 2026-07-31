"""LLM planner: natural-language task → JSON pipeline spec.

The planner is a *single* chat call (not multi-turn) that takes:

  - the user's natural-language task
  - a compact DataFrame profile
  - a markdown rendering of the solver catalog

and returns a strict JSON object of the same shape consumed by
``software1_pipeline_demo_app.run_spec.build_pipeline_from_spec``::

    {
      "rationale": "<one short paragraph>",
      "steps": [
        {"solver": "<id>",
         "from": "previous|initial|step",
         "step_index": <int>,         # only when from=step
         "csv_key": "<key>" or "auto", # only when from=step
         "params": {...},              # solver constructor params (rare)
         "mapping": {role: col|[cols]|{...}}   # any user-fixed roles
        },
        ...
      ]
    }

The mapping field can be empty per step — the runner will fill it
later via the same LLM in :mod:`mapping_engine`.  But if the planner
already knows obvious column names from the profile, it may pre-fill
them; that is treated as a stronger 'manual' override.

中文说明
========
单次 Chat 调用完成「任务理解 → 选算子 → 产出可执行 JSON」。``mapping``
可空，执行时由 ``mapping_engine`` 再补；但若用户在自然语言里写明
路径/分组等，system prompt 要求 **原样抄进 JSON**（mapping fidelity），
否则小模型容易漏导致下游失败。未知算子名会先经 ``_SOLVER_ALIASES``
规范化，无法匹配的记入 ``invalid_solver_ids`` 并中止可执行性。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from operator_library.profiler import profile_df, profile_to_text

from operator_pipeline import llm_client
from operator_pipeline.registry import list_solvers

from operator_agent.catalog import (
    build_catalog_for_planner,
    render_catalog_markdown,
)
from operator_agent.hypothesis import Hypothesis
from operator_agent.hypothesis_agent import (
    HypothesisResult,
    propose_hypothesis,
)
from operator_agent.prompts import (
    build_operator_planner_system_prompt,
    format_operator_planner_user_message,
)

# Stage 3 VERIFY operator-selector system prompt.  Composed from
# operator-selection blocks ONLY — it does NOT see the hypothesis-card
# schema or finding-family few-shot examples.  Those live in the
# physically-separate Stage 2 agent (:mod:`operator_agent.hypothesis_agent`),
# which is invoked first inside ``plan_pipeline`` and whose output is
# passed to this prompt as user-message context (V7 §3.4).
PLANNER_SYSTEM: str = build_operator_planner_system_prompt(
    lang="en",
    include_hypothesis_card=False,
    include_few_shot_families=False,
)

# 规划 LLM 常输出的别名 / 口语化名称 → registry 里的正式 solver id。
# 在校验前就地改写 step["solver"]；未收录的别名进入 invalid_solver_ids。
# 新增算子时：若模型爱用简称，在此补一行可降低规划失败率。
_SOLVER_ALIASES: Dict[str, str] = {
    "hierarchical_clustering":   "hclust_samples",
    "hierarchical_cluster":      "hclust_samples",
    "hclust":                    "hclust_samples",
    "pca":                       "pca_decompose",
    "principal_component_analysis": "pca_decompose",
    "limma":                     "limma_deg_two_group",
    "limma_deg":                 "limma_deg_two_group",
    "differential_expression":   "limma_deg_two_group",
    "deg_two_group":             "limma_deg_two_group",
    "probe_to_gene":             "probe_to_gene_collapse",
    "gene_collapse":             "probe_to_gene_collapse",
    # NOTE: probe_deg_collapse_to_gene operates on a DEG TABLE, not on
    # the raw expression matrix.  Aliases below cover the common ways
    # an LLM might refer to GEO2R-style "best probe per gene" collapse.
    "probe_deg_collapse":        "probe_deg_collapse_to_gene",
    "deg_collapse":              "probe_deg_collapse_to_gene",
    "deg_collapse_to_gene":      "probe_deg_collapse_to_gene",
    "best_probe_per_gene":       "probe_deg_collapse_to_gene",
    "best_probe":                "probe_deg_collapse_to_gene",
    "min_adj_p_per_gene":        "probe_deg_collapse_to_gene",
    "geo2r_collapse":            "probe_deg_collapse_to_gene",
    "soft_parser":               "gds_soft_parser",
    "geo_soft_parser":           "gds_soft_parser",
    "pathway_enrichment":        "pathway_enrichment_fisher",
    "fisher_enrichment":         "pathway_enrichment_fisher",
    "hypergeometric_enrichment": "pathway_enrichment_fisher",
    # DESeq2 / edgeR / batch correction / pgx / MR / mediation common aliases
    "deseq2":                    "deseq2_de",
    "deseq":                     "deseq2_de",
    "deseq2_diff_expr":          "deseq2_de",
    "deseq2_differential":       "deseq2_de",
    "deseq2_differential_expression": "deseq2_de",
    "deseq2_wald":               "deseq2_de",
    "edger":                     "edger_de",
    "edger_diff_expr":           "edger_de",
    "edger_qlf":                 "edger_de",
    "edger_tmm_qlf":             "edger_de",
    "combat":                    "combat_batch_correction",
    "combat_seq":                "combat_batch_correction",
    "batch_correction":          "combat_batch_correction",
    "remove_batch_effect":       "combat_batch_correction",
    "mediation":                 "mediation_analysis",
    "baron_kenny":               "mediation_analysis",
    "mendelian_randomization_mr": "mendelian_randomization",
    "mr":                        "mendelian_randomization",
    "ivw":                       "mendelian_randomization",
    "mr_ivw":                    "mendelian_randomization",
    "pgx":                       "pgx_interaction",
    "drug_snp_interaction":      "pgx_interaction",
    "pharmacogenomics_interaction": "pgx_interaction",
    "snp_drug_interaction":      "pgx_interaction",
    # generic stats common confusions
    "pearson":                   "pearson_correlation",
    "spearman":                  "spearman_correlation",
    "kendall":                   "kendall_correlation",
    "ttest":                     "welch_t_test",
    "t_test":                    "welch_t_test",
    "welch":                     "welch_t_test",
    "anova":                     "oneway_anova",
    "chi2":                      "chi_square_independence",
    "chi_square":                "chi_square_independence",
    "kruskal":                   "kruskal_wallis",
    "mwu":                       "mann_whitney_u_test",
    "mannwhitney":               "mann_whitney_u_test",
    # 中文口语 / 任务框点名（工作台用户不必写英文 id）
    "缺失检查":                   "missing_summary",
    "缺失汇总":                   "missing_summary",
    "描述统计":                   "describe_full",
    "描述性分析":                 "describe_full",
    "分布直方图":                 "distribution_histogram",
    "直方图分析":                 "distribution_histogram",
    "异常值识别":                 "outlier_iqr_flag",
    "异常值检测":                 "outlier_iqr_flag",
    "正态性检验":                 "normality_test",
    "正态性":                     "normality_test",
    "缺失填补":                   "data_imputation",
    "填补缺失":                   "data_imputation",
    "类别编码":                   "encode_categorical",
    "相关分析":                   "pearson_correlation",
    "pearson相关":                "pearson_correlation",
    "皮尔逊相关":                 "pearson_correlation",
    "spearman相关":               "spearman_correlation",
    "斯皮尔曼相关":               "spearman_correlation",
    "分组统计":                   "groupby_stat",
    "t检验":                      "welch_t_test",
    "ｔ检验":                     "welch_t_test",
    "welch检验":                  "welch_t_test",
    "非参数检验":                 "mann_whitney_u_test",
    "mannwhitney检验":            "mann_whitney_u_test",
    "多重校正":                   "multiple_correction",
    "多重比较校正":               "multiple_correction",
    "线性回归":                   "linear_regression",
    "卡方检验":                   "chi_square_independence",
    "方差分析":                   "oneway_anova",
    "差异表达":                   "limma_deg_two_group",
    "主成分分析":                 "pca_decompose",
    # V8 §G: logreg / rf aliases were removed when their target operators
    # (logistic_regression / random_forest) were hidden from the planner
    # as freeloaders.  If the LLM still emits these names they fall into
    # ``invalid_solver_ids`` and the rationale notes the rejection; the
    # planner then naturally routes the prediction step to the Coder.
    "hgb":                       "hist_gradient_boosting",
    "histgb":                    "hist_gradient_boosting",
    "xgb":                       "xgboost",
    "lgbm":                      "lightgbm",
    "psm":                       "propensity_score_matching",
    "cox":                       "cox_regression",
    # V8 W19..W28 aliases — covers the names LLMs commonly hallucinate
    # in place of the registered ids.
    "nma":                       "network_meta_analysis",
    "network_ma":                "network_meta_analysis",
    "frequentist_nma":           "network_meta_analysis",
    "random_effects_nma":        "network_meta_analysis",
    "indirect_comparison":       "network_meta_analysis",
    "mixed_treatment_comparison": "network_meta_analysis",
    "mtc":                       "network_meta_analysis",
    "2pl_irt":                   "irt_calibration",
    "irt":                       "irt_calibration",
    "irt_2pl":                   "irt_calibration",
    "item_response_theory":      "irt_calibration",
    "rasch_model":               "irt_calibration",
    "lgcm":                      "latent_growth_curve",
    "lgm":                       "latent_growth_curve",
    "growth_curve":              "latent_growth_curve",
    "growth_curve_model":        "latent_growth_curve",
    "growth_mixture_model":      "latent_growth_curve",
    "gmm":                       "latent_growth_curve",  # NB collides with mixture model in some contexts; ok here because we have no GaussianMixture solver
    "trajectory_analysis":       "latent_growth_curve",
    "linear_mixed_effects":      "latent_growth_curve",
    "lme":                       "latent_growth_curve",
    "lmer":                      "latent_growth_curve",
    "gxe":                       "prs_x_env_interaction",
    "gene_environment_interaction": "prs_x_env_interaction",
    "prs_gxe":                   "prs_x_env_interaction",
    "prs_env":                   "prs_x_env_interaction",
    "prs_environment_interaction": "prs_x_env_interaction",
    "interaction_analysis":      "prs_x_env_interaction",
    "interaction_regression":    "prs_x_env_interaction",
    "moderator_analysis":        "prs_x_env_interaction",
    "moderation_analysis":       "prs_x_env_interaction",
    "bayesian_hierarchical":     "bayesian_hierarchical_glm",
    "bhglm":                     "bayesian_hierarchical_glm",
    "hierarchical_bayes":        "bayesian_hierarchical_glm",
    "empirical_bayes":           "bayesian_hierarchical_glm",
    "james_stein":               "bayesian_hierarchical_glm",
    "subgroup_shrinkage":        "bayesian_hierarchical_glm",
    "partial_pooling":           "bayesian_hierarchical_glm",
    "multilevel_model":          "bayesian_hierarchical_glm",
    "proportional_odds":         "ordinal_regression",
    "proportional_odds_model":   "ordinal_regression",
    "proportional_odds_regression": "ordinal_regression",
    "ordinal_logit":             "ordinal_regression",
    "ordinal_logistic":          "ordinal_regression",
    "ordered_logit":             "ordinal_regression",
    "cumulative_logit":          "ordinal_regression",
    "polr":                      "ordinal_regression",
    "g_formula":                 "g_formula_tmle",
    "gformula":                  "g_formula_tmle",
    "tmle":                      "g_formula_tmle",
    "targeted_maximum_likelihood": "g_formula_tmle",
    "iptw":                      "g_formula_tmle",
    "ipw":                       "g_formula_tmle",
    "doubly_robust":             "g_formula_tmle",
    "causal_inference":          "g_formula_tmle",
    "ate_estimation":            "g_formula_tmle",
    "average_treatment_effect":  "g_formula_tmle",
    "standardization":           "g_formula_tmle",
    "symptom_network":           "symptom_network_analysis",
    "psychopathology_network":   "symptom_network_analysis",
    "network_analysis":          "symptom_network_analysis",
    "partial_correlation_network": "symptom_network_analysis",
    "graphical_lasso":           "symptom_network_analysis",
    "ggm":                       "symptom_network_analysis",
    "gaussian_graphical_model":  "symptom_network_analysis",
    "qgraph":                    "symptom_network_analysis",
    "bootnet":                   "symptom_network_analysis",
    "joint_model":               "joint_longitudinal_survival",
    "joint_long_surv":           "joint_longitudinal_survival",
    "joint_longitudinal":        "joint_longitudinal_survival",
    "two_stage_joint_model":     "joint_longitudinal_survival",
    "biomarker_survival":        "joint_longitudinal_survival",
    "longitudinal_survival_joint": "joint_longitudinal_survival",
    "jm":                        "joint_longitudinal_survival",
    "fairness_audit":            "disparate_impact_audit",
    "fairness_check":            "disparate_impact_audit",
    "demographic_parity":        "disparate_impact_audit",
    "demographic_parity_diff":   "disparate_impact_audit",
    "disparate_impact":          "disparate_impact_audit",
    "disparate_impact_ratio":    "disparate_impact_audit",
    "equal_opportunity":         "disparate_impact_audit",
    "equalized_odds":            "disparate_impact_audit",
    "four_fifths_rule":          "disparate_impact_audit",
    "bias_audit":                "disparate_impact_audit",
    "group_fairness":            "disparate_impact_audit",
    # V8.1 W29..W33 — aliases the LLM kept emitting during the v8.1 QRData
    # run for the 5 new gap-filling operators (planner hallucinated
    # ``pc_causal_discovery`` and ``direct_lingam`` on causal-discovery
    # questions, so they fell into ``invalid_solver_ids`` instead of
    # routing to the PC / LiNGAM operators).  Pre-empt the same mistake
    # on IV / KM / ARIMA before they bite.
    # -- IV / 2SLS --
    "iv_2sls":                       "instrumental_variable_2sls",
    "iv2sls":                        "instrumental_variable_2sls",
    "2sls":                          "instrumental_variable_2sls",
    "two_stage_least_squares":       "instrumental_variable_2sls",
    "instrumental_variable":         "instrumental_variable_2sls",
    "instrumental_variables":        "instrumental_variable_2sls",
    "iv_regression":                 "instrumental_variable_2sls",
    "iv_estimation":                 "instrumental_variable_2sls",
    "iv_estimate":                   "instrumental_variable_2sls",
    "iv":                            "instrumental_variable_2sls",
    # -- PC algorithm causal discovery --
    "pc":                            "causal_discovery_pc",
    "pc_alg":                        "causal_discovery_pc",
    "pc_algorithm":                  "causal_discovery_pc",
    "pc_causal":                     "causal_discovery_pc",
    "pc_causal_discovery":           "causal_discovery_pc",
    "causal_discovery":              "causal_discovery_pc",
    "causal_dag_discovery":          "causal_discovery_pc",
    "causal_graph_discovery":        "causal_discovery_pc",
    "constraint_based_discovery":    "causal_discovery_pc",
    "spirtes_glymour":               "causal_discovery_pc",
    "spirtes_glymour_pc":            "causal_discovery_pc",
    "full_graph_causal_discovery":   "causal_discovery_pc",
    "dag_discovery":                 "causal_discovery_pc",
    # -- LiNGAM causal discovery (fully oriented) --
    "lingam":                        "causal_discovery_lingam",
    "direct_lingam":                 "causal_discovery_lingam",
    "directlingam":                  "causal_discovery_lingam",
    "lingam_causal_discovery":       "causal_discovery_lingam",
    "lingam_dag":                    "causal_discovery_lingam",
    "non_gaussian_causal_discovery": "causal_discovery_lingam",
    "shimizu_lingam":                "causal_discovery_lingam",
    # -- Kaplan-Meier survival --
    "km":                            "survival_kaplan_meier",
    "kaplan_meier":                  "survival_kaplan_meier",
    "kaplan_meier_estimator":        "survival_kaplan_meier",
    "km_estimator":                  "survival_kaplan_meier",
    "km_survival":                   "survival_kaplan_meier",
    "survival_curve":                "survival_kaplan_meier",
    "survival_curves":               "survival_kaplan_meier",
    "median_survival":               "survival_kaplan_meier",
    "log_rank":                      "survival_kaplan_meier",
    "log_rank_test":                 "survival_kaplan_meier",
    "logrank":                       "survival_kaplan_meier",
    "logrank_test":                  "survival_kaplan_meier",
    "mantel_logrank":                "survival_kaplan_meier",
    "lifelines_kaplan_meier":        "survival_kaplan_meier",
    # -- ARIMA forecast --
    "arima":                         "ts_arima_forecast",
    "auto_arima":                    "ts_arima_forecast",
    "arima_forecast":                "ts_arima_forecast",
    "arima_model":                   "ts_arima_forecast",
    "time_series_forecast":          "ts_arima_forecast",
    "time_series_forecasting":       "ts_arima_forecast",
    "ts_forecast":                   "ts_arima_forecast",
    "univariate_forecast":           "ts_arima_forecast",
    "univariate_forecasting":        "ts_arima_forecast",
    "box_jenkins":                   "ts_arima_forecast",
    # ---- V8.2 W34..W37: bio transcriptomics ----
    # -- B1 differential_expression_limma --
    "differential_expression":        "differential_expression_limma",
    "differential_expression_analysis": "differential_expression_limma",
    "deg":                            "differential_expression_limma",
    "deg_analysis":                   "differential_expression_limma",
    "de_analysis":                    "differential_expression_limma",
    "de":                             "differential_expression_limma",
    "rna_seq_de":                     "differential_expression_limma",
    "rna_seq_differential_expression": "differential_expression_limma",
    "bulk_rnaseq_de":                 "differential_expression_limma",
    "limma":                          "differential_expression_limma",
    "limma_voom":                     "differential_expression_limma",
    "deseq2":                         "differential_expression_limma",
    "pydeseq2":                       "differential_expression_limma",
    "edger":                          "differential_expression_limma",
    "gene_expression_de":             "differential_expression_limma",
    "microarray_de":                  "differential_expression_limma",
    "wald_test_de":                   "differential_expression_limma",
    "negative_binomial_de":           "differential_expression_limma",
    # -- B2 pathway_enrichment_ora --
    "ora":                            "pathway_enrichment_ora",
    "pathway_enrichment":             "pathway_enrichment_ora",
    "pathway_analysis":               "pathway_enrichment_ora",
    "gene_set_enrichment_ora":        "pathway_enrichment_ora",
    "over_representation":            "pathway_enrichment_ora",
    "over_representation_analysis":   "pathway_enrichment_ora",
    "fisher_exact_enrichment":        "pathway_enrichment_ora",
    "hypergeometric_enrichment":      "pathway_enrichment_ora",
    "go_enrichment":                  "pathway_enrichment_ora",
    "kegg_enrichment":                "pathway_enrichment_ora",
    "reactome_enrichment":            "pathway_enrichment_ora",
    "enrichr":                        "pathway_enrichment_ora",
    "msigdb_enrichment":              "pathway_enrichment_ora",
    "hallmark_enrichment":            "pathway_enrichment_ora",
    # -- B3 gsea_preranked --
    "gsea":                           "gsea_preranked",
    "preranked_gsea":                 "gsea_preranked",
    "pre_ranked_gsea":                "gsea_preranked",
    "ranked_gsea":                    "gsea_preranked",
    "gene_set_enrichment_analysis":   "gsea_preranked",
    "gsea_prerank":                   "gsea_preranked",
    "subramanian_gsea":               "gsea_preranked",
    "fgsea":                          "gsea_preranked",
    # -- B4 gene_set_score --
    "ssgsea":                         "gene_set_score",
    "single_sample_gsea":             "gene_set_score",
    "barbie_ssgsea":                  "gene_set_score",
    "pathway_score":                  "gene_set_score",
    "gene_set_scoring":               "gene_set_score",
    "gsva":                           "gene_set_score",   # closest analogue
    "per_sample_pathway_score":       "gene_set_score",
    "sample_level_pathway_score":     "gene_set_score",
}


# Required JSON shape shown to the operator-selector LLM at the bottom
# of the user message.  Crucially, NO ``hypothesis`` field — that is
# the Stage 2 agent's output, not Stage 3's.  If the LLM happens to
# emit one anyway, ``plan_pipeline`` will just ignore it.
#
# V8 §G — the example deliberately AVOIDS ``missing_summary`` (the #1
# freeloader of the V8 retrieval study, planned 17×/consumed 1× across
# 90 items).  Showing it here used to cause the LLM to mimic it as a
# reflex EDA step.  The new example demonstrates the intended pattern:
# a real cleaning operator → a real analysis operator → a Coder finalize
# step that consumes the analysis operator's output.
_EXAMPLE_OUTPUT_JSON: Dict[str, Any] = {
    "rationale": "1 short sentence about the chosen plan",
    "steps": [
        {"solver": "data_imputation",
         "input_source": "main"},
        {"solver": "pearson_correlation",
         "input_source": "step:1.filled_csv"},
        {"solver": "__coder__",
         "input_source": "step:2.pearson_pairs",
         "coder_hint": "Read the upstream correlation pairs CSV and "
                        "print the top-3 most correlated feature pairs "
                        "(|r| largest) as a list inside the FINDINGS "
                        "block."},
    ],
}


def _parse_input_source(src: Any) -> Optional[Dict[str, Any]]:
    """Translate a planner ``input_source`` string into the legacy
    ``from`` / ``step_index`` / ``csv_key`` triple consumed by
    :mod:`operator_pipeline.run_spec`.

    Accepted strings (planner system prompt rule 3):

    - ``"main"`` / ``"initial"`` → ``{"from": "initial"}``
    - ``"previous"`` → ``{"from": "previous"}`` (back-compat only;
      the prompt now discourages this form)
    - ``"step:N"`` → ``{"from": "step", "step_index": N,
      "csv_key": "auto"}``
    - ``"step:N.csv_key"`` → ``{"from": "step", "step_index": N,
      "csv_key": "<csv_key>"}``

    Returns ``None`` when ``src`` is not a string or is empty (callers
    keep any existing ``from`` / ``step_index`` / ``csv_key`` fields).
    """
    if not isinstance(src, str):
        return None
    s = src.strip()
    if not s:
        return None
    sl = s.lower()
    if sl in ("main", "initial", "__initial__"):
        return {"from": "initial"}
    if sl in ("previous", "auto"):
        return {"from": "previous"}
    if sl.startswith("step:") or sl.startswith("step_"):
        body = s.split(":", 1)[1] if ":" in s else s.split("_", 1)[1]
        body = body.strip()
        if not body:
            return None
        if "." in body:
            idx_str, key = body.split(".", 1)
        else:
            idx_str, key = body, "auto"
        try:
            idx = int(idx_str.strip())
        except ValueError:
            return None
        return {"from": "step", "step_index": idx,
                "csv_key": (key.strip() or "auto")}
    return None


def _apply_input_source(s: Dict[str, Any]) -> None:
    """In-place: if ``s["input_source"]`` is set, fill ``from`` /
    ``step_index`` / ``csv_key`` from it (overrides whatever the LLM
    already wrote in the legacy fields, since ``input_source`` is the
    new authoritative field per planner rule 3)."""
    parsed = _parse_input_source(s.get("input_source"))
    if parsed is None:
        return
    for k, v in parsed.items():
        s[k] = v

_CODER_SOLVER_ID = "__coder__"


def operators_disabled() -> bool:
    """A/B-benchmark switch: when env ``PIPELINE_NO_OPERATORS`` is truthy
    (``1``/``true``/``yes``/``on``), the pipeline runs in *coder-only*
    baseline mode — operator selection and execution are skipped entirely
    and the whole task is handed to the Coder.  Unset (default) keeps the
    normal full pipeline behaviour, so existing callers are unaffected.

    中文：A/B 基线开关。``PIPELINE_NO_OPERATORS=1`` 时关闭算子，整个任务
    交给 coder（纯 LLM 基线）；未设置时行为与原来完全一致。
    """
    return os.environ.get("PIPELINE_NO_OPERATORS", "").strip().lower() in (
        "1", "true", "yes", "on")


@dataclass
class PlanResult:
    spec: Dict[str, Any]
    rationale: str
    raw: Dict[str, Any]
    valid_solver_ids: List[str] = field(default_factory=list)
    invalid_solver_ids: List[str] = field(default_factory=list)
    # Steps that name a real solver (executable by operator runner).
    operator_steps: List[Dict[str, Any]] = field(default_factory=list)
    # Steps explicitly delegated to the Coder via solver="__coder__".
    coder_steps: List[Dict[str, Any]] = field(default_factory=list)
    # V8 §C — Stage 2 HYPOTHESIZE structured output.  None for generic
    # data tasks (e.g. "draw a heatmap") that don't test a scientific
    # claim.  Validation errors on the hypothesis field do NOT fail the
    # plan; they are surfaced via ``hypothesis_warning``.
    hypothesis: Optional[Hypothesis] = None
    hypothesis_warning: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        if self.error is not None:
            return False
        return bool(self.operator_steps) or bool(self.coder_steps)

    @property
    def has_operator_steps(self) -> bool:
        return bool(self.operator_steps)

    @property
    def has_coder_steps(self) -> bool:
        return bool(self.coder_steps)


def plan_pipeline(task: str,
                   df: pd.DataFrame,
                   max_tokens: int = 6000,
                   temperature: float = 0.0,
                   *,
                   skip_hypothesis: bool = False) -> PlanResult:
    """Two-agent planning entry point (Stage 2 HYPOTHESIZE + Stage 3 VERIFY).

    Internally makes **two** LLM calls:

    1. Stage 2 — :func:`operator_agent.hypothesis_agent.propose_hypothesis`
       (sees ONLY task + dataframe profile + hypothesis-card rule +
       finding-family few-shots).  Emits a structured ``Hypothesis``
       card or ``None`` for generic data tasks.
    2. Stage 3 — this function's LLM call to the operator selector
       (sees ONLY task + dataframe profile + Stage-2 hypothesis as
       context + operator catalog + operator-selection rules).  Emits
       the executable pipeline JSON.

    The two LLMs never share a prompt; each only sees what its
    responsibility requires (V7 §3.3-3.4 agent boundaries).

    Args
    ----
    task, df
        User task and dataframe.
    max_tokens, temperature
        Knobs for the Stage 3 LLM call.  Stage 2 uses half the token
        budget (or 800 minimum) since its output is much smaller.
    skip_hypothesis
        If True, skip the Stage 2 LLM call entirely and run Stage 3
        unconditioned.  Useful for tests of operator selection in
        isolation, or for downstream pipelines that already have a
        hypothesis from elsewhere.

    Returns
    -------
    PlanResult
        Combined result.  ``PlanResult.hypothesis`` is populated from
        Stage 2 (not from Stage 3's output); ``PlanResult.spec`` and
        ``PlanResult.operator_steps`` are populated from Stage 3.
        Either stage's failure is surfaced via ``error`` /
        ``hypothesis_warning`` without breaking the other.
    """
    # === A/B baseline short-circuit: operators OFF (coder-only). ===
    # When PIPELINE_NO_OPERATORS is set we skip BOTH planning LLM calls
    # (Stage-2 hypothesis AND Stage-3 operator selection) and return an
    # empty plan.  Drivers always run the Coder afterwards regardless of
    # whether the plan has operator/coder steps, so an empty plan routes
    # the ENTIRE task to the Coder — a pure-LLM baseline whose token cost
    # reflects coder-only.  Zero operator steps is verifiable from the
    # returned PlanResult (operator_steps == [] and coder_steps == []).
    if operators_disabled():
        return PlanResult(
            spec={"steps": []},
            rationale="[PIPELINE_NO_OPERATORS] operators disabled; "
                      "coder solves the whole task (baseline).",
            raw={"steps": [], "no_operators": True},
            operator_steps=[],
            coder_steps=[],
            error=None,
        )

    if not llm_client.is_available():
        return PlanResult(
            spec={"steps": []},
            rationale="",
            raw={},
            error="LLM not configured (.env missing API key/base URL)",
        )

    # === Stage 2 HYPOTHESIZE (separate agent + separate LLM call). ===
    # The Stage-2 agent has its own prompt (hypothesis_card + finding
    # family few-shots only); it never sees the operator catalog.
    # Result is passed to Stage 3 below as user-message context per V7
    # §3.4.  If Stage 2 fails or returns None (generic task), Stage 3
    # still runs unconditioned.
    if skip_hypothesis:
        hyp_result = HypothesisResult()
    else:
        hyp_result = propose_hypothesis(
            task, df,
            max_tokens=max(1200, max_tokens // 3),
            temperature=temperature,
        )

    hypothesis_ctx: Optional[Dict[str, Any]] = None
    if hyp_result.hypothesis is not None:
        hypothesis_ctx = hyp_result.hypothesis.to_dict()

    # === Stage 3 VERIFY operator selection (this agent's job). ===
    catalog = build_catalog_for_planner()
    profile = profile_df(df)
    profile_text = profile_to_text(profile, max_lines=120)

    # V8 retrieval study: when OP_RETRIEVAL is enabled, retrieve only the
    # top-k relevant operators for (task + dataframe profile) and show ONLY
    # those to the planner — instead of the full ~70-operator catalog.  The
    # `__coder__` fallback is unaffected (it is not a catalog operator and is
    # always available via the prompt rules + always run last by drivers).
    retrieval_note = ""
    try:
        from operator_agent import op_retrieval as _opr
        _method = _opr.get_method()
        if _method != "none":
            _query = (task.strip() + "\n"
                      + " ".join(map(str, list(df.columns)[:60])) + "\n"
                      + profile_text)
            _keep = _opr.retrieve_operator_ids(_query, method=_method)
            if _keep:
                _keep_set = set(_keep)
                _filtered = [c for c in catalog if c.get("id") in _keep_set]
                if _filtered:
                    catalog = _filtered
                    retrieval_note = (
                        f"[op_retrieval={_method} k={len(_filtered)}: "
                        + ", ".join(_keep) + "]")
    except Exception as _e:  # retrieval must never break planning
        retrieval_note = f"[op_retrieval-error: {type(_e).__name__}: {_e}]"

    catalog_md = render_catalog_markdown(catalog)

    user_msg = format_operator_planner_user_message(
        task=task.strip(),
        profile_text=profile_text,
        catalog_md=catalog_md,
        example=json.dumps(_EXAMPLE_OUTPUT_JSON, ensure_ascii=False, indent=2),
        lang="en",
        hypothesis_context=hypothesis_ctx,
    )

    try:
        raw = llm_client.chat_json(PLANNER_SYSTEM, user_msg,
                                    max_tokens=max_tokens,
                                    temperature=temperature,
                                    stage="planner")
    except llm_client.LLMError as e:
        return PlanResult(
            spec={"steps": []}, rationale="", raw={},
            hypothesis=hyp_result.hypothesis,
            hypothesis_warning=hyp_result.warning,
            error=f"planner LLM call failed: {e}",
        )

    if not isinstance(raw, dict) or "steps" not in raw:
        return PlanResult(spec={"steps": []}, rationale="", raw=raw,
                          error="planner output missing 'steps' key")
    if not isinstance(raw["steps"], list):
        return PlanResult(spec={"steps": []}, rationale="",
                          raw=raw,
                          error="planner output 'steps' is not a list")

    valid = {sid for sid, _ in list_solvers()}
    chosen: List[str] = []
    aliased: List[str] = []
    invalid: List[str] = []
    auto_coderified: List[str] = []
    dropped_output_keys: List[str] = []
    operator_steps: List[Dict[str, Any]] = []
    coder_steps: List[Dict[str, Any]] = []

    # All output-file keys across the entire catalog.  LLMs sometimes
    # confuse the column-key of a solver's *output file* (e.g.
    # ``ranking_csv``, ``edge_list_csv``) with a separate solver to chain.
    # We detect this and drop the step as a no-op because the file is
    # already produced by the upstream solver.
    _all_output_keys: set = set()
    for sig in catalog:
        for k in (sig.get("outputs") or {}):
            _all_output_keys.add(k.lower())

    # LLM 经常把可视化步骤写成 ``volcano_plot`` / ``heatmap`` 这种伪算子名而非 ``__coder__``。
    # 这里捕获 "plot/viz/figure/draw/visual" 类未知名字, 自动转为 Coder 兜底步骤。
    _viz_keywords = ("plot", "heatmap", "barchart", "barplot", "scatter",
                     "boxplot", "violin", "histogram", "viz", "visual",
                     "figure", "draw", "render", "chart", "diagram",
                     "manhattan", "volcano", "forest", "venn", "umap",
                     "tsne", "report_pdf", "export_pdf")

    def _looks_like_viz(name: str) -> bool:
        n = name.lower().replace("-", "_")
        return any(kw in n for kw in _viz_keywords)

    for s in raw["steps"]:
        if not isinstance(s, dict):
            continue
        _apply_input_source(s)
        orig = str(s.get("solver", "")).strip()

        if orig == _CODER_SOLVER_ID:
            hint = str(s.get("coder_hint") or "").strip()
            if not hint:
                # Fall back to step description if planner forgot the hint.
                hint = str(s.get("description") or s.get("step") or "").strip()
            if not hint:
                invalid.append(f"{_CODER_SOLVER_ID}(missing coder_hint)")
                chosen.append(orig)
                continue
            s["coder_hint"] = hint
            coder_steps.append(s)
            chosen.append(orig)
            continue

        if orig in valid:
            operator_steps.append(s)
            chosen.append(orig)
            continue
        # 中文别名（如「描述统计」）与英文小写别名一并匹配
        canon = _SOLVER_ALIASES.get(orig) or _SOLVER_ALIASES.get(orig.lower())
        if canon and canon in valid:
            s["solver"] = canon
            aliased.append(f"{orig} -> {canon}")
            operator_steps.append(s)
            chosen.append(canon)
            continue

        # Drop steps that are just an output-file key of some solver.
        # E.g. LLM emits ``{"solver": "ranking_csv"}`` after a real
        # ``network_meta_analysis`` step — the file was already produced.
        if orig.lower() in _all_output_keys:
            dropped_output_keys.append(orig)
            continue

        # Auto-coderify visualization-like unknowns.
        if _looks_like_viz(orig):
            hint = str(s.get("coder_hint") or s.get("description") or s.get("step") or "").strip()
            if not hint:
                hint = (f"Produce a `{orig}` figure from the upstream step "
                        f"(read its CSV output); save as {orig}.png in the workspace.")
            s["solver"] = _CODER_SOLVER_ID
            s["coder_hint"] = hint
            auto_coderified.append(f"{orig} -> __coder__")
            coder_steps.append(s)
            chosen.append(_CODER_SOLVER_ID)
            continue

        invalid.append(orig)
        chosen.append(orig)

    error = None
    if invalid:
        error = ("planner referenced unknown solvers: "
                 + ", ".join(invalid))

    notes: List[str] = []
    if retrieval_note:
        notes.append(retrieval_note)
    if aliased:
        notes.append("[planner-aliased: " + "; ".join(aliased) + "]")
    if auto_coderified:
        notes.append("[planner-coderified: " + "; ".join(auto_coderified) + "]")
    if dropped_output_keys:
        notes.append("[planner-dropped-output-keys: "
                     + "; ".join(dropped_output_keys) + "]")

    # V8 hypothesis card now comes from the *separate* Stage 2 agent
    # (see ``propose_hypothesis`` call above).  We surface its warnings
    # here, and attach the result to PlanResult for back-compat.
    hypothesis = hyp_result.hypothesis
    hypo_warn = hyp_result.warning
    if hyp_result.error:
        notes.append(f"[hypothesis-agent-error: {hyp_result.error}]")
    if hypothesis is not None and getattr(hypothesis, "warnings", None):
        notes.append("[hypothesis-agent-warnings: "
                     + "; ".join(hypothesis.warnings) + "]")
    if hypo_warn is not None:
        notes.append(f"[hypothesis-agent-rejected: {hypo_warn}]")

    spec_out: Dict[str, Any] = {"steps": raw["steps"]}
    if hypothesis is not None:
        spec_out["hypothesis"] = hypothesis.to_dict()

    return PlanResult(
        spec=spec_out,
        rationale=str(raw.get("rationale", ""))
                  + (("\n" + "\n".join(notes)) if notes else ""),
        raw=raw,
        valid_solver_ids=[s for s in chosen if s in valid],
        invalid_solver_ids=invalid,
        operator_steps=operator_steps,
        coder_steps=coder_steps,
        hypothesis=hypothesis,
        hypothesis_warning=hypo_warn,
        error=error,
    )
